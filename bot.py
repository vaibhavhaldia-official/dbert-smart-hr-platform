"""
bot.py — Gayatri Education Project: Google Form → SMTP Email Bot

Polls a Google Sheet for form submissions. Sends a result email to every non-enrolled
applicant who hasn't already received one, newest entries first.

Flow:
  1. Read all rows from Google Sheet
  2. Sort by timestamp (newest first)
  3. For each row:
     - Skip if email is in enrollment.csv
     - Skip if confirmation_sent_at is already set
     - Build profile from form data
     - Send result email
     - Mark as sent in sheet + DB

All sends go through smtp_service.send_and_log() which:
  - Picks a random eligible sender (under hourly + daily rate limits)
  - Retries on failure with exponential backoff
  - Logs every send to the email_log table
  - Records hourly + daily send counts per sender
"""

import time
import logging
import random
import sys
import traceback
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from bot_config import (
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_GID,
    SERVICE_ACCOUNT_KEY,
    POLL_INTERVAL_SECONDS,
    WHATSAPP_GROUP_URL,
    APPLICATION_URL,
    RESULTS_URL,
    RESULT_SUBJECT,
    RESULT_BODY_HTML,
    RESULT_BODY_TEXT,
    COL_EMAIL,
    COL_CONFIRMATION_SENT,
    COL_NAME,
    COL_INTERNSHIP,
    COL_COLLEGE,
    COL_SEMESTER,
    COL_WHATSAPP,
    COL_AVAILABLE,
    COL_LAPTOP,
    COL_PLACEMENT,
    COL_CV,
    COL_HEAR_ABOUT,
    HR_INDUCTION_DATE,
)
from smtp_service import SenderPool, send_and_log, throttle

from send_control import is_blocked, get_global_pause_info, get_bot_pause_info
from database import upsert_applicant, log_email_send, update_confirmation_sent_at, init_database
from enrollment_filter import is_enrolled, reload as reload_enrollments

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bot")


# ============================================================
# GOOGLE SHEETS HELPERS
# ============================================================

def connect_sheet():
    """Connect to the Google Sheet and return the worksheet object."""
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_KEY, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.get_worksheet_by_id(int(GOOGLE_SHEET_GID))

    sheet_name = spreadsheet.title
    tab_name = worksheet.title
    logger.info(f"Connected to sheet: {sheet_name} (tab: {tab_name}, gid: {GOOGLE_SHEET_GID})")
    return worksheet


def get_all_rows(worksheet):
    """Fetch all rows as a list of lists, including header."""
    return worksheet.get_all_values()


def find_col_index(headers, col_name):
    """Find the 0-based column index for a header name (case-insensitive)."""
    for i, h in enumerate(headers):
        if h.strip().lower() == col_name.strip().lower():
            return i
    return None


def _col_idx_to_letter(idx):
    """Convert 0-based column index to spreadsheet column letter (A, B, ..., Z, AA, AB, ...)."""
    if idx < 26:
        return chr(65 + idx)
    result = ""
    while idx >= 0:
        idx, remainder = divmod(idx, 26)
        result = chr(65 + remainder) + result
        idx -= 1
    return result


def ensure_columns(worksheet, headers, required_cols):
    """Ensure required columns exist in the sheet header row.
    If any are missing, append them to the end. Returns updated headers list."""
    missing = []
    for col_name in required_cols:
        if col_name not in [h.strip() for h in headers]:
            missing.append(col_name)

    if missing:
        logger.info(f"Creating missing columns in sheet: {missing}")
        current_len = len(headers)
        for i, col_name in enumerate(missing):
            # Convert index to spreadsheet column letter (handles 26+ columns)
            col_letter = _col_idx_to_letter(current_len + i)
            cell = f"{col_letter}1"
            worksheet.update(range_name=cell, values=[[col_name]])
            logger.info(f"  Added column '{col_name}' at {cell}")

        new_headers = worksheet.row_values(1)
        return new_headers

    return headers


# ============================================================
# EMAIL CONTENT BUILDER (no CV/LLM)
# ============================================================

def build_result_email(name, domain="", applicant=None):
    """
    Build a confirmation/result email from form data only. No CV analysis, no LLM.
    Returns (subject, html_body, text_body).
    """
    from database import get_all_ctas

    subject = RESULT_SUBJECT

    # Default domain display
    display_domain = domain if domain else "your selected track"

    # Fetch dynamic CTAs (safe failure — uses empty list on error)
    cta_list = []
    try:
        cta_list = get_all_ctas(email_type="result") or []
    except Exception as e:
        logger.warning(f"Failed to load CTAs for result email: {e}")
    cta_buttons_html = ""
    if cta_list:
        if len(cta_list) == 1:
            c = cta_list[0]
            cta_buttons_html = f'''
                <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 28px 0;">
                  <tr>
                    <td align="center" style="background-color: {c['bg_color']}; border-radius: 6px; padding: 14px 20px;">
                      <a href="{c['url']}" style="color: {c['text_color']}; text-decoration: none; font-size: 15px; font-weight: bold; display: block;">{c.get('icon','')} {c['label']}</a>
                    </td>
                  </tr>
                </table>'''
        else:
            half = (len(cta_list) + 1) // 2
            left = cta_list[:half]
            right = cta_list[half:]
            max_rows = max(len(left), len(right))
            rows_html = ""
            for i in range(max_rows):
                left_btn = left[i] if i < len(left) else None
                right_btn = right[i] if i < len(right) else None
                rows_html += "  <tr>\n"
                rows_html += '    <td width="48%" style="padding-right: 8px;">\n'
                if left_btn:
                    rows_html += f'      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center" style="background-color: {left_btn["bg_color"]}; border-radius: 6px; padding: 14px 20px;"><a href="{left_btn["url"]}" style="color: {left_btn["text_color"]}; text-decoration: none; font-size: 15px; font-weight: bold; display: block;">{left_btn.get("icon","")} {left_btn["label"]}</a></td></tr></table>'
                rows_html += "\n    </td>\n"
                rows_html += '    <td width="4%">&nbsp;</td>\n'
                rows_html += '    <td width="48%" style="padding-left: 8px;">\n'
                if right_btn:
                    rows_html += f'      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center" style="background-color: {right_btn["bg_color"]}; border-radius: 6px; padding: 14px 20px;"><a href="{right_btn["url"]}" style="color: {right_btn["text_color"]}; text-decoration: none; font-size: 15px; font-weight: bold; display: block;">{right_btn.get("icon","")} {right_btn["label"]}</a></td></tr></table>'
                rows_html += "\n    </td>\n  </tr>\n"
            cta_buttons_html = f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 28px 0;">\n{rows_html}</table>'

    # --- Render HTML ---
    html_body = RESULT_BODY_HTML.format(
        name=name,
        domain=display_domain,
        cta_buttons=cta_buttons_html,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        results_url=RESULTS_URL,
        induction_date=HR_INDUCTION_DATE,
    )

    # --- Render plain text ---
    text_body = RESULT_BODY_TEXT.format(
        name=name,
        domain=display_domain,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        results_url=RESULTS_URL,
        induction_date=HR_INDUCTION_DATE,
    )

    return subject, html_body, text_body


# ============================================================
# MAIN BOT LOOP
# ============================================================

def process_sheet(worksheet, pool):
    """
    Read the sheet, sort newest-first, send emails to non-enrolled applicants
    who haven't received one yet.

    Returns (emails_sent, failures).
    """
    emails_sent = 0
    failures = 0

    rows = get_all_rows(worksheet)
    if len(rows) < 2:
        logger.info("Sheet has no data rows yet.")
        return 0, 0

    headers = rows[0]

    # Ensure bot-managed columns exist
    headers = ensure_columns(worksheet, headers, [COL_CONFIRMATION_SENT])

    data_rows = rows[1:]

    # Resolve column indices
    col_email = find_col_index(headers, COL_EMAIL)
    col_name = find_col_index(headers, COL_NAME)
    col_conf_sent = find_col_index(headers, COL_CONFIRMATION_SENT)

    if col_email is None:
        logger.error("Required column 'Email Address' not found — bot cannot operate.")
        return 0, 0
    if col_name is None:
        logger.error("Required column 'Name' not found — bot cannot operate.")
        return 0, 0

    # Resolve optional columns
    optional_cols = {}
    for col_name_opt, col_key in [
        (COL_INTERNSHIP, "internship"),
        (COL_COLLEGE, "college"),
        (COL_SEMESTER, "semester"),
        (COL_WHATSAPP, "whatsapp"),
        (COL_AVAILABLE, "available"),
        (COL_LAPTOP, "laptop"),
        (COL_PLACEMENT, "placement"),
        (COL_CV, "cv"),
        (COL_HEAR_ABOUT, "hear_about"),
        ("Timestamp", "timestamp"),
    ]:
        optional_cols[col_key] = find_col_index(headers, col_name_opt)

    # Build a list of (timestamp, row_index, row) so we can sort newest-first
    _TS_FORMATS = [
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M:%S %p",
    ]

    def get_timestamp(row):
        ts_col = optional_cols.get("timestamp")
        if ts_col is not None and ts_col < len(row):
            ts = row[ts_col].strip()
            if ts:
                for fmt in _TS_FORMATS:
                    try:
                        return datetime.strptime(ts, fmt)
                    except ValueError:
                        continue
        return datetime.min

    # Pair each row with its index (for sheet row number) and sort newest first
    indexed_rows = list(enumerate(data_rows))
    indexed_rows.sort(key=lambda x: get_timestamp(x[1]), reverse=True)

    logger.info(f"Processing {len(indexed_rows)} rows (newest first)")

    for i, row in indexed_rows:
        row_num = i + 2  # 1-based, row 1 is header

        # Pad row if needed
        while len(row) <= max(col_email, col_name, col_conf_sent or 0):
            row.append("")

        email = row[col_email].strip() if col_email < len(row) else ""
        name = row[col_name].strip() if col_name < len(row) else ""

        if not email or not name:
            logger.debug(f"Row {row_num}: skipping — no email or name")
            continue

        # --- Skip enrolled ---
        if is_enrolled(email):
            logger.debug(f"[Enrolled] Skipping {email} — in enrollment.csv")
            continue

        # --- Skip if already sent ---
        conf_sent = row[col_conf_sent].strip() if col_conf_sent is not None and col_conf_sent < len(row) else ""
        if conf_sent:
            logger.debug(f"[Already Sent] Skipping {email} — confirmation_sent_at = '{conf_sent}'")
            continue

        # --- Sync to DB ---
        try:
            domain_val = row[optional_cols["internship"]].strip() if optional_cols.get("internship") is not None and optional_cols["internship"] < len(row) else ""
            college_val = row[optional_cols["college"]].strip() if optional_cols.get("college") is not None and optional_cols["college"] < len(row) else ""
            semester_val = row[optional_cols["semester"]].strip() if optional_cols.get("semester") is not None and optional_cols["semester"] < len(row) else ""
            whatsapp_val = row[optional_cols["whatsapp"]].strip() if optional_cols.get("whatsapp") is not None and optional_cols["whatsapp"] < len(row) else ""
            available_val = row[optional_cols["available"]].strip() if optional_cols.get("available") is not None and optional_cols["available"] < len(row) else ""
            laptop_val = row[optional_cols["laptop"]].strip() if optional_cols.get("laptop") is not None and optional_cols["laptop"] < len(row) else ""
            placement_val = row[optional_cols["placement"]].strip() if optional_cols.get("placement") is not None and optional_cols["placement"] < len(row) else ""
            cv_val = row[optional_cols["cv"]].strip() if optional_cols.get("cv") is not None and optional_cols["cv"] < len(row) else ""
            hear_val = row[optional_cols["hear_about"]].strip() if optional_cols.get("hear_about") is not None and optional_cols["hear_about"] < len(row) else ""
            ts_val = row[optional_cols["timestamp"]].strip() if optional_cols.get("timestamp") is not None and optional_cols["timestamp"] < len(row) else ""

            applicant_data = {
                "email": email,
                "name": name,
                "internship": domain_val,
                "college": college_val,
                "semester": semester_val,
                "whatsapp": whatsapp_val,
                "available": available_val,
                "laptop": laptop_val,
                "placement": placement_val,
                "cv": cv_val,
                "hear_about": hear_val,
                "approval_status": "Pending",
                "timestamp": ts_val,
            }
            upsert_applicant(applicant_data)
        except Exception as e:
            logger.warning(f"Failed to sync row {row_num} ({email}) to DB: {e}")

        # --- Send result email ---
        logger.info(f"[Email] Sending to {email} ({name}) | domain: {domain_val or 'none selected'}")
        subject, html_body, text_body = build_result_email(name, domain_val)

        success, sender_email = send_and_log(
            pool=pool,
            to_addr=email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            phase="result",
        )

        if success:
            sent_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Update Google Sheet
            if col_conf_sent is not None:
                try:
                    worksheet.update_cell(row_num, col_conf_sent + 1, sent_time)
                except Exception as e:
                    logger.error(f"Failed to update sheet row {row_num}: {e}")

            # Update DB
            try:
                update_confirmation_sent_at(email, sent_time)
            except Exception as e:
                logger.error(f"Failed to update DB for {email}: {e}")

            emails_sent += 1
            throttle()
        else:
            failures += 1

    logger.info(f"--- Poll done at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Emails sent: {emails_sent}, Failures: {failures} ---")
    return emails_sent, failures


def main():
    """Main bot loop — polls the sheet every POLL_INTERVAL_SECONDS."""
    logger.info("=" * 60)
    logger.info("Gayatri Education Project — Email Bot Starting (SMTP)")
    logger.info("=" * 60)

    # Initialize database tables if they don't exist
    try:
        init_database()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        sys.exit(1)

    # Connect to Google Sheet
    try:
        worksheet = connect_sheet()
    except Exception as e:
        logger.error(f"Failed to connect to Google Sheet: {e}")
        logger.error("Check your service account JSON key and sheet sharing permissions.")
        sys.exit(1)

    pool = SenderPool()
    logger.info(f"Polling every {POLL_INTERVAL_SECONDS}s. Press Ctrl+C to stop.")
    logger.info("")

    total_sent = 0
    total_failures = 0
    consecutive_errors = 0

    while True:
        try:
            # Check if sending is paused (global or bot-specific) BEFORE reload
            if is_blocked("bot"):
                if get_global_pause_info()[0]:
                    pause_type = "Global"
                else:
                    pause_type = "Bot"
                logger.info(f"--- {pause_type} send PAUSED — skipping poll cycle ---")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Refresh enrollment list in case enrollment.csv was updated
            try:
                reload_enrollments()
            except Exception as e:
                logger.warning(f"Failed to reload enrollments: {e}")

            logger.info(f"--- Poll started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
            sent, fails = process_sheet(worksheet, pool)
            total_sent += sent
            total_failures += fails
            consecutive_errors = 0
            logger.info(f"--- Totals | Sent: {total_sent}, Failures: {total_failures} ---")

        except KeyboardInterrupt:
            logger.info("Bot stopped by user (Ctrl+C).")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Unexpected error in poll cycle: {e}")
            logger.error(traceback.format_exc())
            # Reconnect after 3 consecutive failures
            if consecutive_errors >= 3:
                logger.warning(f"Reconnecting to Google Sheet after {consecutive_errors} consecutive failures...")
                try:
                    worksheet = connect_sheet()
                    consecutive_errors = 0
                    logger.info("Reconnected successfully.")
                except Exception as reconnect_err:
                    logger.error(f"Reconnection failed: {reconnect_err}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
