"""
smtp_sender.py — Bulk Email Campaign Engine for Gayatri Education Project

Sends reminder emails via SMTP with:
- 4 rotating sender addresses, 20 emails/hr each (360/day max)
- Random sender selection
- 30–60s delay between sends
- Resume safety (picks up where it left off)
- FOMO-themed HTML templates personalized from DB data

Uses the shared smtp_service module for SMTP sending, rate limiting,
and retry logic — all flows (result emails, campaigns, progress)
share the same sender pool and rate-limit tables.
"""

import time
import random
import logging
import traceback
from datetime import datetime

from smtp_service import (
    SMTPSender,
    SenderPool,
    send_email_with_retry,
    throttle,
    send_and_log,
)
from bot_config import (
    SENDER_POOL,
    SMTP_MIN_DELAY_SECONDS,
    SMTP_MAX_DELAY_SECONDS,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    RATE_LIMIT_PER_SENDER_PER_HOUR,
    REMINDER_SUBJECTS,
    REMINDER_BODY_HTML,
    REMINDER_BODY_TEXT,
    APPLICATION_URL,
    WHATSAPP_GROUP_URL,
    RESULTS_URL,
    HR_INDUCTION_DATE,
)
from send_control import is_blocked, get_global_pause_info
from database import (
    get_campaign,
    update_campaign,
    increment_campaign_sent,
    increment_campaign_failed,
    get_sender_count,
    increment_sender_count,
    get_hour_bucket,
    get_reminder_campaign_targets,
    cleanup_old_sender_stats,
    log_email_send,
)

logger = logging.getLogger("smtp_sender")


# ============================================================
# EMAIL BUILDERS
# ============================================================

def build_reminder_email(applicant):
    """
    Build reminder email personalized from DB data.

    Returns (subject, html_body, text_body).
    """
    domain = applicant.get("internship_domain") or "our program"
    name = applicant.get("name", "Candidate")

    # Pick a random subject variant
    subject = random.choice(REMINDER_SUBJECTS)

    # Fill in the template
    html_body = REMINDER_BODY_HTML.format(
        name=name,
        domain=domain,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        results_url=RESULTS_URL,
        induction_date=HR_INDUCTION_DATE,
    )
    text_body = REMINDER_BODY_TEXT.format(
        name=name,
        domain=domain,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        results_url=RESULTS_URL,
        induction_date=HR_INDUCTION_DATE,
    )

    return subject, html_body, text_body


# ============================================================
# CAMPAIGN RUNNER
# ============================================================

def run_campaign(campaign_id):
    """
    Main campaign loop. Runs in a daemon thread.

    Continuously picks eligible applicants, sends reminder emails,
    and updates stats until all are processed or campaign is paused/stopped.
    """
    pool = SenderPool()
    campaign = get_campaign(campaign_id)
    if not campaign:
        logger.error(f"Campaign {campaign_id} not found")
        return

    logger.info(f"Starting campaign {campaign_id}: {campaign['campaign_name']}")
    update_campaign(campaign_id,
                    status="running",
                    started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # Clean up old sender stats
    cleanup_old_sender_stats()

    targets = get_reminder_campaign_targets()
    total = len(targets)
    update_campaign(campaign_id, total_queued=total)
    logger.info(f"Campaign {campaign_id}: {total} eligible applicants")

    sent = 0
    failed = 0

    for applicant in targets:
        # Check if campaign was paused/stopped
        campaign = get_campaign(campaign_id)
        if not campaign or campaign["status"] in ("paused", "stopped"):
            logger.info(f"Campaign {campaign_id} {campaign.get('status', 'unknown')} — pausing loop")
            if campaign and campaign["status"] == "paused":
                return  # will be resumed later
            update_campaign(campaign_id, status="stopped",
                            completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            return

        # Check if sending is paused (global or campaigns-specific)
        if is_blocked("campaigns"):
            if get_global_pause_info()[0]:
                pause_type = "Global"
            else:
                pause_type = "Campaigns"
            logger.info(f"{pause_type} send PAUSED — waiting 30s before re-checking")
            time.sleep(30)
            continue

        # Build email
        subject, html_body, text_body = build_reminder_email(applicant)

        # Pick sender (shared pool enforces hourly + daily limits)
        sender = pool.pick_sender()
        if sender is None:
            # All senders rate-limited — wait with periodic status checks
            logger.info("All senders rate-limited. Waiting with status checks...")
            waited = 0
            max_wait = 300
            while waited < max_wait:
                time.sleep(30)
                waited += 30
                # Check if campaign was paused/stopped during wait
                campaign = get_campaign(campaign_id)
                if not campaign or campaign["status"] in ("paused", "stopped"):
                    logger.info(f"Campaign {campaign_id} {campaign.get('status', 'unknown')} during rate-limit wait — exiting")
                    if campaign and campaign["status"] == "paused":
                        return
                    update_campaign(campaign_id, status="stopped",
                                    completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    return
                # Re-check if a sender became available
                sender = pool.pick_sender()
                if sender:
                    logger.info(f"Sender available after {waited}s wait")
                    break
            if sender is None:
                continue

        # Send
        to_addr = applicant.get("email", "")
        if not to_addr:
            logger.warning(f"Applicant {applicant['id']} has no email, skipping")
            try:
                increment_campaign_failed(campaign_id)
            except Exception as e:
                logger.warning(f"Failed to increment campaign failed: {e}")
            continue

        success = send_email_with_retry(sender, to_addr, subject, html_body, text_body)

        if success:
            try:
                pool.increment(sender.email)
            except Exception as e:
                logger.warning(f"Failed to increment sender count: {e}")
            try:
                increment_campaign_sent(campaign_id)
            except Exception as e:
                logger.warning(f"Failed to increment campaign sent: {e}")
            # Also mark email_sent_at in applicants table for dedup
            try:
                update_email_sent_at(to_addr, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as e:
                logger.warning(f"Failed to update email_sent_at for {to_addr}: {e}")
            # Log the send
            try:
                log_email_send(
                    to_email=to_addr,
                    from_email=sender.email,
                    subject=subject,
                    body_preview=text_body[:200],
                    phase="reminder",
                    campaign_id=campaign_id,
                    status="sent",
                )
            except Exception as e:
                logger.warning(f"Failed to log campaign send for {to_addr}: {e}")
            sent += 1
            logger.info(f"[{sent}/{total}] Sent to {to_addr} via {sender.email}")
        else:
            try:
                increment_campaign_failed(campaign_id)
            except Exception as e:
                logger.warning(f"Failed to increment campaign failed: {e}")
            failed += 1
            logger.warning(f"[{sent}/{total}] FAILED for {to_addr}")
            # Log the failure
            try:
                log_email_send(
                    to_email=to_addr,
                    from_email=sender.email if sender else "",
                    subject=subject,
                    body_preview=text_body[:200],
                    phase="reminder",
                    campaign_id=campaign_id,
                    status="failed",
                    error_message="SMTP send failed after max retries",
                )
            except Exception as e:
                logger.warning(f"Failed to log campaign failure for {to_addr}: {e}")

        # Update last sender info for dashboard
        try:
            update_campaign(campaign_id,
                            last_sender_email=sender.email if sender else "",
                            last_sender_sent_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:
            logger.warning(f"Failed to update campaign last_sender info: {e}")

        # Throttle between sends
        if sent + failed < total:
            delay = random.randint(SMTP_MIN_DELAY_SECONDS, SMTP_MAX_DELAY_SECONDS)
            logger.info(f"Throttling: waiting {delay}s...")
            time.sleep(delay)

    # Done
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_campaign(campaign_id,
                    status="completed",
                    completed_at=now_str)
    logger.info(f"Campaign {campaign_id} completed: {sent} sent, {failed} failed")


def get_campaign_live_stats(campaign_id):
    """Return live stats for dashboard display."""
    from bot_config import SENDER_POOL
    campaign = get_campaign(campaign_id)
    if not campaign:
        return None

    pool_stats = SenderPool().get_stats()
    sender_view = []
    for s in SENDER_POOL:
        sent = pool_stats.get(s["email"], 0)
        sender_view.append({
            "email": s["email"],
            "name": s.get("name", ""),
            "sent": sent,
            "limit": RATE_LIMIT_PER_SENDER_PER_HOUR,
            "remaining": max(0, RATE_LIMIT_PER_SENDER_PER_HOUR - sent),
        })

    return {
        "campaign": campaign,
        "sender_stats": sender_view,
        "limit_per_sender": RATE_LIMIT_PER_SENDER_PER_HOUR,
    }
