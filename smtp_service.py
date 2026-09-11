"""
smtp_service.py — Shared SMTP sending service for all email flows.

Used by:
  - bot.py          (result-based emails)
  - smtp_sender.py  (bulk reminder campaigns)

All flows share the same SenderPool and rate-limit tables, ensuring
the per-sender hourly cap (default 25/hr) is never exceeded across
result emails and reminders combined.
"""

import smtplib
import time
import random
import logging
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from ssl import create_default_context

from bot_config import (
    SMTP_MIN_DELAY_SECONDS,
    SMTP_MAX_DELAY_SECONDS,
    SENDER_POOL,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    DEFAULT_REPLY_TO,
)
from database import (
    get_sender_count,
    increment_sender_count,
    get_sender_daily_count,
    increment_sender_daily_count,
    get_hour_bucket,
    log_email_send,
    update_email_sent_at,
    get_email_log_stats,
    mark_email_bounced,
)

logger = logging.getLogger("smtp_service")


# ============================================================
# LOW-LEVEL SMTP SENDER
# ============================================================

class SMTPSender:
    """Wraps smtplib with TLS, retries, and connection caching."""

    def __init__(self, sender_config):
        self.email = sender_config["email"]
        self.password = sender_config["password"]
        self.name = sender_config.get("name", "")
        self.server = sender_config.get("server", "smtp.gayatrieducation.info")
        self.port = sender_config.get("port", 587)
        self.use_tls = sender_config.get("use_tls", True)

    def send(self, to_addr, subject, html_body, text_body):
        """Send a single email via SMTP. Returns True on success, raises on failure."""
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.name} <{self.email}>"
        msg["To"] = to_addr
        msg["Reply-To"] = DEFAULT_REPLY_TO
        msg["Subject"] = subject
        msg["MIME-Version"] = "1.0"
        msg["X-Mailer"] = "Gayatri Education Bot"
        msg["X-Priority"] = "3"
        msg["X-MSMail-Priority"] = "Normal"
        msg["Importance"] = "Normal"
        msg["List-Unsubscribe"] = f"<mailto:{DEFAULT_REPLY_TO}?subject=unsubscribe>"

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        context = create_default_context()
        # Port 465 = implicit SSL (SMTP_SSL); other ports = explicit STARTTLS
        if self.port == 465:
            with smtplib.SMTP_SSL(self.server, self.port, context=context) as server:
                server.login(self.email, self.password)
                server.sendmail(self.email, to_addr, msg.as_string())
        else:
            with smtplib.SMTP(self.server, self.port) as server:
                if self.use_tls:
                    server.starttls(context=context)
                server.login(self.email, self.password)
                server.sendmail(self.email, to_addr, msg.as_string())

        return True


def send_email_with_retry(smtp_sender, to_addr, subject, html_body, text_body):
    """Send with retry logic. Returns True on success, False on permanent failure."""
    from bot_config import MAX_RETRIES, RETRY_BACKOFF_BASE
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            smtp_sender.send(to_addr, subject, html_body, text_body)
            logger.info(f"Sent to {to_addr} via {smtp_sender.email} — '{subject}'")
            return True
        except smtplib.SMTPSenderRefused as e:
            logger.error(f"Permanent rejection for {to_addr}: {e}")
            _handle_bounce(to_addr, "sender_refused", str(e))
            return False
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Auth failed for sender {smtp_sender.email}: {e}")
            _handle_bounce(to_addr, "auth_failed", str(e))
            return False
        except smtplib.SMTPRecipientsRefused as e:
            logger.error(f"Recipient refused for {to_addr}: {e}")
            _handle_bounce(to_addr, "recipient_refused", str(e))
            return False
        except smtplib.SMTPDataError as e:
            logger.error(f"SMTP data error for {to_addr}: {e}")
            _handle_bounce(to_addr, "data_error", str(e))
            return False
        except Exception as e:
            logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed for {to_addr}: {e}")
            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)
            else:
                _handle_bounce(to_addr, "send_failed", str(e)[:200])
                logger.error(f"All {MAX_RETRIES} attempts failed for {to_addr}")
                return False
    return False


def _handle_bounce(to_addr: str, bounce_type: str, reason: str):
    """Log a bounce/delivery failure to the email_log table."""
    try:
        mark_email_bounced(to_addr, bounce_type, reason[:200])
        logger.info(f"Logged bounce for {to_addr}: {bounce_type}")
    except Exception as e:
        logger.warning(f"Could not log bounce for {to_addr}: {e}")


# ============================================================
# SENDER POOL — shared by all flows
# ============================================================

class SenderPool:
    """Manages the pool of SMTP senders with rate limiting."""

    def __init__(self):
        self._senders = {s["email"]: SMTPSender(s) for s in SENDER_POOL}

    def get_eligible_senders(self):
        """Return list of sender emails under BOTH hourly and daily caps."""
        from bot_config import RATE_LIMIT_PER_SENDER_PER_HOUR, RATE_LIMIT_PER_SENDER_PER_DAY
        from datetime import datetime
        now_bucket = get_hour_bucket()
        today_bucket = datetime.now().strftime("%Y-%m-%d")
        eligible = []
        for email in self._senders:
            hour_count = get_sender_count(email, now_bucket)
            day_count = get_sender_daily_count(email, today_bucket)
            if hour_count < RATE_LIMIT_PER_SENDER_PER_HOUR and day_count < RATE_LIMIT_PER_SENDER_PER_DAY:
                eligible.append(email)
        return eligible

    def pick_sender(self):
        """Pick a random eligible sender (under both hourly AND daily limits). Returns None if all capped."""
        from bot_config import RATE_LIMIT_PER_SENDER_PER_HOUR, RATE_LIMIT_PER_SENDER_PER_DAY
        from datetime import datetime
        now_bucket = get_hour_bucket()
        today_bucket = datetime.now().strftime("%Y-%m-%d")
        eligible = []
        for email in self._senders:
            hour_count = get_sender_count(email, now_bucket)
            day_count = get_sender_daily_count(email, today_bucket)
            if hour_count < RATE_LIMIT_PER_SENDER_PER_HOUR and day_count < RATE_LIMIT_PER_SENDER_PER_DAY:
                eligible.append(email)
        if not eligible:
            return None
        chosen = random.choice(eligible)
        return self._senders[chosen]

    def get_stats(self):
        """Return dict of sender_email -> count for current hour."""
        now_bucket = get_hour_bucket()
        return {email: get_sender_count(email, now_bucket) for email in self._senders}

    def increment(self, sender_email):
        """Record one send for the given sender (hourly + daily)."""
        from datetime import datetime
        hour_bucket = get_hour_bucket()
        day_bucket = datetime.now().strftime("%Y-%m-%d")
        increment_sender_count(sender_email, hour_bucket)
        increment_sender_daily_count(sender_email, day_bucket)

    def wait_for_available(self, max_wait_seconds=3600):
        """
        Block until at least one sender is available.
        Checks every 60s. Returns the chosen SMTPSender or None on timeout.
        """
        waited = 0
        while waited < max_wait_seconds:
            sender = self.pick_sender()
            if sender:
                return sender
            logger.info("All senders rate-limited. Waiting 60s before retry...")
            time.sleep(60)
            waited += 60
        return None


# ============================================================
# HIGH-LEVEL SEND + LOG
# ============================================================

def send_and_log(pool, to_addr, subject, html_body, text_body, phase="result",
                 campaign_id=None, from_email=""):
    """
    Pick an eligible sender, send the email, log it, and return (success, sender_email).

    This is the single entry point for all email sends — bot.py and smtp_sender.py
    both call this. This guarantees:
      - Rate limits are shared across all flows
      - Every send is logged to email_log
      - Delays are applied between sends
    """
    sender = pool.pick_sender()
    if sender is None:
        logger.warning("All senders rate-limited. Cannot send now.")
        return False, ""

    success = send_email_with_retry(sender, to_addr, subject, html_body, text_body)

    if success:
        pool.increment(sender.email)
        # Log to email_log
        try:
            log_email_send(
                to_email=to_addr,
                from_email=sender.email,
                subject=subject,
                body_preview=text_body[:200],
                phase=phase,
                campaign_id=campaign_id,
                status="sent",
            )
        except Exception as e:
            logger.warning(f"Failed to log email_send for {to_addr}: {e}")
        # Update confirmation_sent_at for result emails
        if phase == "result":
            try:
                from database import update_confirmation_sent_at
                update_confirmation_sent_at(to_addr, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as e:
                logger.warning(f"Failed to update confirmation_sent_at for {to_addr}: {e}")
    else:
        try:
            log_email_send(
                to_email=to_addr,
                from_email=from_email or (sender.email if sender else ""),
                subject=subject,
                body_preview=text_body[:200],
                phase=phase,
                campaign_id=campaign_id,
                status="failed",
                error_message="SMTP send failed after max retries",
            )
        except Exception as e:
            logger.warning(f"Failed to log email_fail for {to_addr}: {e}")

    return success, sender.email if sender else ""


def throttle():
    """Apply a random delay between sends to avoid IP blocking."""
    delay = random.randint(SMTP_MIN_DELAY_SECONDS, SMTP_MAX_DELAY_SECONDS)
    logger.info(f"Throttling: waiting {delay}s...")
    time.sleep(delay)


# ============================================================
# HELPERS
# ============================================================

def _get_rate_limit():
    """Return the per-sender hourly rate limit from config."""
    from bot_config import RATE_LIMIT_PER_SENDER_PER_HOUR
    return RATE_LIMIT_PER_SENDER_PER_HOUR


def get_pool_stats():
    """Return stats dict for all senders (current hour)."""
    pool = SenderPool()
    raw = pool.get_stats()
    limit = _get_rate_limit()
    result = []
    for s in SENDER_POOL:
        sent = raw.get(s["email"], 0)
        result.append({
            "email": s["email"],
            "name": s.get("name", ""),
            "sent": sent,
            "limit": limit,
            "remaining": max(0, limit - sent),
        })
    return result
