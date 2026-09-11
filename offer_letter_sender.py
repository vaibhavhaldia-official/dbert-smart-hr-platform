"""
offer_letter_sender.py — Offer Letter Campaign for Gayatri Education Project

Reads selected2.csv, generates personalized PDF offer letters from sampleoffer.docx,
and sends them via the shared SMTP sender pool with rate limiting, enrollment
filtering, retry logic, logging, and pause/stop control.

PDF pipeline:
  1. python-docx placeholder replacement on sampleoffer.docx
  2. MS Word COM: DOCX -> PDF (uses pywin32, already in requirements)
  3. pypdf metadata embedding (offer_ref, candidate email, send date, sender)

Reuses:
  - smtp_service.SenderPool for rate-limited sender selection
  - smtp_service.send_and_log() for sending + email_log recording
  - enrollment_filter.is_enrolled() for CSV guard
  - database functions for tracking offer_sent_at
"""

import os
import re
import time
import random
import traceback
import uuid
import logging
import tempfile
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from ssl import create_default_context
from datetime import datetime, timedelta
from pathlib import Path

from docx import Document
from pypdf import PdfReader, PdfWriter

from bot_config import (
    SENDER_POOL,
    SMTP_MIN_DELAY_SECONDS,
    SMTP_MAX_DELAY_SECONDS,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    RATE_LIMIT_PER_SENDER_PER_HOUR,
    APPLICATION_URL,
    WHATSAPP_GROUP_URL,
)
from database import (
    get_campaign,
    update_campaign,
    increment_campaign_sent,
    increment_campaign_failed,
    get_all_sender_stats,
    cleanup_old_sender_stats,
    log_email_send,
    update_offer_sent_at,
    get_offer_letter_targets,
    create_campaign,
)
from enrollment_filter import is_enrolled
from smtp_service import SenderPool, throttle
from send_control import is_blocked, get_global_pause_info

logger = logging.getLogger("offer_letter")

# Add file handler so errors from daemon threads are captured
if not logger.handlers:
    _fh = logging.FileHandler("offer_letter.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)

# ============================================================
# CONFIGURATION
# ============================================================

SELECTED2_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selected2.csv")
SAMPLE_OFFER_DOCX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sampleoffer.docx")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_offers")

HR_INDUCTION_DATE = (datetime.now() + timedelta(days=(7 - datetime.now().weekday()) % 7 or 7)).strftime("%d %b %Y")

STIPEND_FIXED = "5,000"
STIPEND_PERFORMANCE = "13,000"
STIPEND_TOTAL = "18,000"
HOURS_PER_WEEK = "10"
WORK_MODE = "Fully Remote (Work from Home)"

# Subject line pool — randomized for deliverability
OFFER_SUBJECTS = [
    "Your Internship Offer — Gayatri Education, {name}",
    "Welcome to Gayatri Education — Offer Letter, {name}",
    "Offer Letter: Gayatri Education Internship Program — {name}",
]

OFFER_BODY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Internship Offer Letter — Gayatri Education</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f5f5f5; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333; line-height: 1.6;">

  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f5f5f5; padding: 30px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e0e0e0;">

          <!-- HEADER -->
          <tr>
            <td style="background-color: #1e3a5f; padding: 28px 40px; text-align: center;">
              <p style="margin: 0; font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: 0.5px;">GAYATRI EDUCATION</p>
              <p style="margin: 6px 0 0; font-size: 13px; color: #90cdf4;">Internship Offer Letter</p>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="padding: 36px 40px;">

              <p style="font-size: 16px; color: #1e3a5f; margin: 0 0 18px 0;">Dear <strong>{name}</strong>,</p>

              <p style="font-size: 15px; color: #444444; margin: 0 0 16px 0;">
                {intro}
              </p>

              <p style="font-size: 15px; color: #444444; margin: 0 0 24px 0;">
                Your offer letter is attached as a PDF. Please review all terms carefully — stipend, tenure, and other conditions are detailed inside.
              </p>

              <!-- IMPORTANT NOTE -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0;">
                <tr>
                  <td style="background-color: #eff6ff; border-left: 4px solid #2563eb; padding: 16px 20px; border-radius: 4px;">
                    <p style="margin: 0; font-size: 14px; color: #1e40af;">
                      <strong>Important:</strong> Your internship slot will be confirmed only after you complete the enrollment form below.
                    </p>
                  </td>
                </tr>
              </table>

              <!-- STEP 1 -->
              <p style="font-size: 14px; color: #444444; margin: 0 0 8px 0;">
                <strong>Step 1:</strong> Reply to this email with <strong>"I accept the offer"</strong> to confirm your participation.
              </p>

              <!-- STEP 2 + CTA -->
              <p style="font-size: 14px; color: #444444; margin: 0 0 16px 0;">
                <strong>Step 2:</strong> Complete the enrollment form to secure your spot.
              </p>

              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0;">
                <tr>
                  <td align="center">
                    <a href="{application_url}" style="display: inline-block; background-color: #2563eb; color: #ffffff; padding: 14px 36px; border-radius: 6px; text-decoration: none; font-size: 15px; font-weight: 600;">Complete Enrollment</a>
                  </td>
                </tr>
              </table>

              <p style="font-size: 13px; color: #666666; margin: 0 0 24px 0; text-align: center;">
                Please complete enrollment by <strong>{start_date}</strong>. Unconfirmed slots may be reassigned to waitlisted candidates.
              </p>

              <!-- INTERNSHIP DETAILS TABLE -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0; border-collapse: collapse;">
                <tr style="background-color: #1e3a5f;">
                  <td colspan="2" style="padding: 10px 16px; font-size: 14px; font-weight: 700; color: #ffffff; border-radius: 4px 4px 0 0;">Internship Details</td>
                </tr>
                <tr style="background-color: #f8fafc;">
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #475569; font-size: 13px; width: 40%;">Domain</td>
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; color: #333333; font-size: 14px;">{domain}</td>
                </tr>
                <tr>
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #475569; font-size: 13px;">Duration</td>
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; color: #333333; font-size: 14px;">2 Months ({start_date} to {end_date})</td>
                </tr>
                <tr style="background-color: #f8fafc;">
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #475569; font-size: 13px;">Work Mode</td>
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; color: #333333; font-size: 14px;">{work_mode}</td>
                </tr>
                <tr>
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #475569; font-size: 13px;">Hours / Week</td>
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; color: #333333; font-size: 14px;">{hours_per_week} hours (flexible)</td>
                </tr>
                <tr style="background-color: #f8fafc;">
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; font-weight: 600; color: #475569; font-size: 13px;">Stipend</td>
                  <td style="padding: 10px 16px; border: 1px solid #e2e8f0; color: #333333; font-size: 14px;">
                    Fixed: &#8377;{stipend_fixed} / month<br>
                    Performance Linked: &#8377;{stipend_performance} / month<br>
                    <strong>Total: &#8377;{stipend_total} / month</strong>
                  </td>
                </tr>
              </table>

              <!-- HR INDUCTION -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0;">
                <tr>
                  <td style="background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 16px 20px; border-radius: 4px;">
                    <p style="margin: 0; font-size: 14px; color: #92400e;">
                      <strong>HR Induction:</strong> All selected interns must attend the HR induction on <strong>{induction_date}</strong>.<br>
                      The security deposit of &#8377;499 will be collected on the date of joining and is fully refundable if you decide not to continue.
                    </p>
                  </td>
                </tr>
              </table>

              <!-- WHATSAPP -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0;">
                <tr>
                  <td style="padding: 16px 20px; background-color: #f0fdf4; border-left: 4px solid #22c55e; border-radius: 4px;">
                    <p style="margin: 0; font-size: 14px; color: #166534;">
                      <strong>Join our WhatsApp Community:</strong> Connect with your batch mates, stay updated on announcements and schedule changes.
                    </p>
                    <p style="margin: 10px 0 0;">
                      <a href="{whatsapp_url}" style="display: inline-block; background-color: #25D366; color: #ffffff; padding: 10px 24px; border-radius: 6px; text-decoration: none; font-size: 14px; font-weight: 600;">Join WhatsApp Group</a>
                    </p>
                  </td>
                </tr>
              </table>

              <!-- OUTRO -->
              <p style="font-size: 15px; color: #444444; margin: 0 0 20px 0;">
                {outro}
              </p>

              <p style="font-size: 15px; color: #444444; margin: 0;">
                Best regards,<br>
                <strong>Gayatri Education HR Team</strong><br>
                Gayatri Education
              </p>

            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background-color: #f8f9fa; padding: 20px 40px; text-align: center; border-top: 1px solid #e9ecef;">
              <p style="font-size: 12px; color: #888888; margin: 0;">
                Gayatri Education Project &copy; 2026. All rights reserved.<br>
                <a href="mailto:contactus@dbert.online" style="color: #666666; text-decoration: none;">contactus@dbert.online</a>
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>

</body>
</html>
"""

OFFER_BODY_TEXT = """\
Dear {name},

{intro}

Your offer letter is attached as a PDF. Please review all terms carefully.

IMPORTANT: Your internship slot will be confirmed only after you complete the enrollment form.
Reply with "I accept the offer" and complete enrollment: {application_url}

Complete enrollment by {start_date} to secure your slot.

INTERNSHIP DETAILS:
- Domain: {domain}
- Duration: 2 Months ({start_date} to {end_date})
- Work Mode: {work_mode}
- Hours per Week: {hours_per_week} hours (flexible)
- Stipend: Fixed Rs. {stipend_fixed}/month + Performance Linked Rs. {stipend_performance}/month = Total Rs. {stipend_total}/month

HR INDUCTION: {induction_date}
The security deposit of Rs. 499 will be collected on the date of joining and is fully refundable if you decide not to continue.

JOIN OUR WHATSAPP COMMUNITY:
Link: {whatsapp_url}

{outro}

Best regards,
Gayatri Education HR Team
Gayatri Education
contactus@dbert.online
"""

EMAIL_INTROS = [
    "Congratulations! You have been selected for the internship program at Gayatri Education. "
    "We are excited to have you on board and look forward to your contributions.",

    "Great news! After reviewing your application, we are pleased to offer you an internship "
    "position at Gayatri Education. We were impressed by your profile.",

    "Welcome to Gayatri Education! We are delighted to confirm your selection for our "
    "internship program. Your official offer letter is attached.",
]

EMAIL_OUTROS = [
    'Please reply with "I accept the offer" to confirm your participation and secure your seat.',
    'Kindly confirm your acceptance by replying to this email so we can proceed with your enrollment.',
    'To secure your internship slot, reply with "I accept the offer" and complete the enrollment form.',
    'We look forward to having you join us. Please reply with your acceptance to begin onboarding.',
]


# ============================================================
# PDF GENERATION
# ============================================================

def _replace_placeholders_in_docx(doc_path, replacements):
    """Replace placeholders in a DOCX template and return the Document object."""
    doc = Document(doc_path)

    def replace_in_paragraph(paragraph):
        if not paragraph.text.strip():
            return
        # Build full inline text from all runs
        inline_text = "".join(run.text for run in paragraph.runs)
        for key, value in replacements.items():
            if key in inline_text:
                inline_text = inline_text.replace(key, value)
        # Clear runs and write back
        for run in paragraph.runs:
            run.text = ""
        if paragraph.runs:
            paragraph.runs[0].text = inline_text
        else:
            paragraph.add_run(inline_text)

    def replace_in_table(table):
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    replace_in_paragraph(paragraph)

    for paragraph in doc.paragraphs:
        replace_in_paragraph(paragraph)
    for table in doc.tables:
        replace_in_table(table)
    for section in doc.sections:
        for header_para in section.header.paragraphs:
            replace_in_paragraph(header_para)
        for footer_para in section.footer.paragraphs:
            replace_in_paragraph(footer_para)

    return doc


def _convert_docx_to_pdf(docx_path, pdf_path):
    """Convert DOCX to PDF using MS Word COM automation (pywin32)."""
    import win32com.client
    import pythoncom

    pythoncom.CoInitialize()
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(os.path.abspath(docx_path)))
        doc.SaveAs(str(os.path.abspath(pdf_path)), FileFormat=17)  # 17 = wdFormatPDF
        doc.Close()
        word.Quit()
    finally:
        pythoncom.CoUninitialize()

    if not os.path.exists(pdf_path):
        raise RuntimeError("Word did not produce a PDF for %s" % docx_path)


def _embed_pdf_metadata(pdf_path, metadata: dict):
    """Embed custom metadata into a PDF using pypdf."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    # Build metadata dict with standard + custom keys
    meta = {f"/{k}": str(v) for k, v in metadata.items()}
    # Preserve any existing metadata
    if reader.metadata:
        for k, v in reader.metadata.items():
            if k not in meta and k not in ("/Producer", "/Creator"):
                meta[k] = v

    writer.add_metadata(meta)
    with open(pdf_path, "wb") as f:
        writer.write(f)
    logger.debug(f"Embedded metadata in {pdf_path}")


def generate_offer_pdf(applicant: dict, offer_ref: str) -> str:
    """
    Generate a personalized offer letter PDF for the given applicant.

    Steps:
      1. Replace placeholders in sampleoffer.docx
      2. MS Word COM: DOCX -> PDF (pywin32)
      3. Embed metadata via pypdf

    Returns the path to the generated PDF.
    """
    name = applicant.get("name", "Candidate")
    email = applicant.get("email", "")
    domain_raw = applicant.get("internship_domain", "")
    start_date_raw = applicant.get("submitted_at", "") or applicant.get("start_date", "")

    # Parse start date
    start_date_str = _parse_start_date(start_date_raw)
    start_dt = datetime.strptime(start_date_str, "%d-%b-%Y")
    end_dt = start_dt + timedelta(days=60)
    end_date_str = end_dt.strftime("%d-%b-%Y")

    # Handle blank domain
    if not domain_raw or domain_raw.strip().lower() in ("", "nan", "none"):
        domain_raw = "To be selected during enrollment"

    domain_display = str(domain_raw).strip()

    # Build replacements for DOCX template
    replacements = {
        "{name}": name,
        "{email}": email,
        "{date}": start_date_str,
        "{start_date}": start_date_str,
        "{end_date}": end_date_str,
        "{company_name}": "Gayatri Education",
        "{internship}": domain_display,
        "{stipend_fixed}": STIPEND_FIXED,
        "{stipend_performance}": STIPEND_PERFORMANCE,
        "{stipend_total}": STIPEND_TOTAL,
        "{hours_per_week}": HOURS_PER_WEEK,
        "{work_mode}": WORK_MODE,
        "{whatsapp_group_link}": WHATSAPP_GROUP_URL,
        "{lms_portal_link}": APPLICATION_URL,
        "{enrollment_link}": APPLICATION_URL,
    }

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Sanitize filename
    safe_name = re.sub(r"[^\w\s-]", "", name).strip()
    safe_name = re.sub(r"[\s]+", "_", safe_name)
    final_pdf = os.path.join(OUTPUT_DIR, f"offer_{safe_name}_{offer_ref[:8]}.pdf")

    # Step 1: DOCX placeholder replacement
    doc = _replace_placeholders_in_docx(SAMPLE_OFFER_DOCX, replacements)

    # Save to temp file
    tmp_dir = tempfile.mkdtemp(prefix="offer_")
    tmp_docx = os.path.join(tmp_dir, f"temp_{uuid.uuid4().hex}.docx")
    doc.save(tmp_docx)

    # Step 2: MS Word COM: DOCX -> PDF (saves directly to final path)
    _convert_docx_to_pdf(tmp_docx, final_pdf)

    # Step 3: Embed metadata
    _embed_pdf_metadata(final_pdf, {
        "offer_ref": offer_ref,
        "candidate_email": email,
        "candidate_name": name,
        "domain": domain_display,
        "start_date": start_date_str,
        "end_date": end_date_str,
        "send_date": datetime.now().strftime("%Y-%m-%d"),
        "sender": "Gayatri Education HR Team",
        "company": "Gayatri Education",
    })

    # Cleanup temp files
    for f in [tmp_docx]:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass
    try:
        if os.path.exists(tmp_dir):
            os.rmdir(tmp_dir)
    except Exception:
        pass

    return final_pdf


def _parse_start_date(raw: str) -> str:
    """Parse a start date from various formats → '01-Apr-2026'.
    If blank/unparseable, defaults to the next Monday.
    """
    raw = str(raw).strip()
    if raw and raw.lower() not in ("", "nan", "none"):
        # selected2.csv uses dd/mm/yyyy or dd-mm-yyyy
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.strftime("%d-%b-%Y")
            except ValueError:
                continue
        logger.warning(f"Could not parse date '{raw}', defaulting to next Monday")

    # Compute next Monday from today
    today = datetime.now()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7  # if today is Monday, go to next Monday
    next_monday = today + timedelta(days=days_until_monday)
    logger.info(f"No start date provided, using next Monday: {next_monday.strftime('%d-%b-%Y')}")
    return next_monday.strftime("%d-%b-%Y")


# ============================================================
# EMAIL BUILDERS
# ============================================================

def build_offer_email(applicant: dict, start_date_str: str, end_date_str: str):
    """Build the offer email (subject, html, text) for an applicant."""
    name = applicant.get("name", "Candidate")
    domain_display = applicant.get("internship_domain", "your selected track")
    if not domain_display or str(domain_display).strip().lower() in ("", "nan", "none"):
        domain_display = "your selected track"

    intro = random.choice(EMAIL_INTROS)
    outro = random.choice(EMAIL_OUTROS)
    subject = random.choice(OFFER_SUBJECTS).format(name=name, start_date=start_date_str)

    html_body = OFFER_BODY_HTML.format(
        name=name,
        intro=intro,
        domain=domain_display,
        start_date=start_date_str,
        end_date=end_date_str,
        induction_date=HR_INDUCTION_DATE,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        work_mode=WORK_MODE,
        hours_per_week=HOURS_PER_WEEK,
        stipend_fixed=STIPEND_FIXED,
        stipend_performance=STIPEND_PERFORMANCE,
        stipend_total=STIPEND_TOTAL,
        outro=outro,
    )

    text_body = OFFER_BODY_TEXT.format(
        name=name,
        intro=intro,
        domain=domain_display,
        start_date=start_date_str,
        end_date=end_date_str,
        induction_date=HR_INDUCTION_DATE,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        work_mode=WORK_MODE,
        hours_per_week=HOURS_PER_WEEK,
        stipend_fixed=STIPEND_FIXED,
        stipend_performance=STIPEND_PERFORMANCE,
        stipend_total=STIPEND_TOTAL,
        outro=outro,
    )

    return subject, html_body, text_body


# ============================================================
# CAMPAIGN RUNNER
# ============================================================

_campaign_threads = {}


def run_offer_campaign(campaign_id: int):
    """
    Main offer letter campaign loop. Runs in a daemon thread.

    For each selected intern:
      1. Generate personalized PDF offer letter
      2. Send via SMTP pool with rate limiting
      3. Update offer_sent_at in DB
      4. Log every send to email_log

    Supports pause/stop via campaign status polling.
    """
    try:
        pool = SenderPool()
        campaign = get_campaign(campaign_id)
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found")
            return

        logger.info(f"Starting offer letter campaign {campaign_id}: {campaign['campaign_name']}")
        update_campaign(campaign_id,
                        status="running",
                        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Clean up old sender stats
        cleanup_old_sender_stats()

        # Load selected2.csv for start_date data
        import csv as csv_mod
        csv_data = {}
        try:
            with open(SELECTED2_CSV, newline="", encoding="utf-8") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    email = row.get("email", "").strip().lower()
                    if email:
                        csv_data[email] = row
            logger.info(f"Loaded {len(csv_data)} rows from selected2.csv")
        except Exception as e:
            logger.warning(f"Could not load selected2.csv: {e}")

        # Get targets: all selected2.csv entries not already sent + not enrolled
        from enrollment_filter import is_enrolled
        from database import get_connection

        already_sent = set()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM applicants WHERE offer_sent_at IS NOT NULL")
        for row in cursor.fetchall():
            if row["email"]:
                already_sent.add(row["email"].lower().strip())
        
        # Also check email_log to catch candidates not in the applicants table or missing offer_sent_at
        cursor.execute("SELECT to_email FROM email_log WHERE phase = 'offer_letter' AND status = 'sent'")
        for row in cursor.fetchall():
            if row["to_email"]:
                already_sent.add(row["to_email"].lower().strip())
        conn.close()

        targets = []
        skipped_enrolled = 0
        skipped_sent = 0
        for email, row in csv_data.items():
            if email in already_sent:
                skipped_sent += 1
                continue
            if is_enrolled(email):
                skipped_enrolled += 1
                continue
            # Build applicant dict from CSV row with field name mapping
            applicant = dict(row)
            applicant["email"] = email
            # CSV uses "internship", code uses "internship_domain"
            if "internship" in applicant and "internship_domain" not in applicant:
                applicant["internship_domain"] = applicant["internship"]
            targets.append(applicant)

        total = len(targets)
        update_campaign(campaign_id, total_queued=total)
        logger.info(f"Offer campaign {campaign_id}: {total} eligible (csv={len(csv_data)}, already_sent={skipped_sent}, enrolled={skipped_enrolled})")

        if total == 0:
            logger.warning(f"Campaign {campaign_id}: no eligible targets found")
            update_campaign(campaign_id, status="completed",
                            error_message="No eligible applicants found (none have result email or all are enrolled)")
            return

        sent = 0
        failed = 0
        offer_ref = uuid.uuid4().hex

        for idx, applicant in enumerate(targets):
            # --- Check pause/stop ---
            campaign = get_campaign(campaign_id)
            if not campaign or campaign["status"] in ("paused", "stopped"):
                logger.info(f"Campaign {campaign_id} {campaign.get('status', 'unknown')} — pausing loop")
                if campaign and campaign["status"] == "paused":
                    return  # will be resumed later
                update_campaign(campaign_id, status="stopped",
                                completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                return

            # Check if sending is paused (global or offers-specific)
            if is_blocked("offers"):
                if get_global_pause_info()[0]:
                    pause_type = "Global"
                else:
                    pause_type = "Offers"
                logger.info(f"{pause_type} send PAUSED — waiting 30s before re-checking")
                time.sleep(30)
                continue

            to_addr = applicant.get("email", "")
            name = applicant.get("name", "Candidate")

            if not to_addr:
                logger.warning(f"Applicant {applicant.get('id')} has no email, skipping")
                increment_campaign_failed(campaign_id)
                failed += 1
                continue

            # --- Merge start_date from selected2.csv ---
            csv_row = csv_data.get(to_addr.lower(), {})
            start_date_raw = csv_row.get("start_date", applicant.get("submitted_at", ""))
            start_date_str = _parse_start_date(start_date_raw)
            start_dt = datetime.strptime(start_date_str, "%d-%b-%Y")
            end_dt = start_dt + timedelta(days=60)
            end_date_str = end_dt.strftime("%d-%b-%Y")

            # --- Generate PDF ---
            pdf_path = None
            try:
                pdf_path = generate_offer_pdf(applicant, offer_ref)
                logger.info(f"[{sent + failed + 1}/{total}] Generated PDF for {to_addr}: {pdf_path}")
            except Exception as e:
                logger.error(f"Failed to generate PDF for {to_addr}: {e}")
                increment_campaign_failed(campaign_id)
                failed += 1
                log_email_send(
                    to_email=to_addr,
                    from_email="",
                    subject="",
                    body_preview="",
                    phase="offer_letter",
                    campaign_id=campaign_id,
                    status="failed",
                    error_message=f"PDF generation failed: {e}",
                )
                continue

            # --- Build email ---
            subject, html_body, text_body = build_offer_email(applicant, start_date_str, end_date_str)

            # --- Pick sender ---
            sender = pool.pick_sender()
            if sender is None:
                # All senders rate-limited — wait with periodic status checks
                logger.info("All senders rate-limited. Waiting with status checks...")
                waited = 0
                max_wait = 300
                while waited < max_wait:
                    time.sleep(30)
                    waited += 30
                    campaign = get_campaign(campaign_id)
                    if not campaign or campaign["status"] in ("paused", "stopped"):
                        logger.info(f"Campaign {campaign_id} {campaign.get('status', 'unknown')} during rate-limit wait — exiting")
                        if campaign and campaign["status"] == "paused":
                            return
                        update_campaign(campaign_id, status="stopped",
                                        completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        return
                    sender = pool.pick_sender()
                    if sender:
                        logger.info(f"Sender available after {waited}s wait")
                        break
                if sender is None:
                    continue

            # --- Send email with attachment ---
            success = _send_offer_email_with_attachment(
                smtp_sender=sender,
                to_addr=to_addr,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
                pdf_path=pdf_path,
                pool=pool,
                campaign_id=campaign_id,
                sequence_num=sent + failed + 1,
                total=total,
            )

            if success:
                sent += 1
                sent_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                try:
                    update_offer_sent_at(to_addr, sent_time)
                except Exception as e:
                    logger.warning(f"Failed to update offer_sent_at for {to_addr}: {e}")
            else:
                failed += 1

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

            # Cleanup PDF after send (keep in generated_offers/ for records)
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except Exception as e:
                    logger.warning(f"Could not delete temp PDF {pdf_path}: {e}")

        # Done
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            update_campaign(campaign_id,
                            status="completed",
                            completed_at=now_str)
        except Exception as e:
            logger.warning(f"Failed to mark campaign {campaign_id} completed: {e}")
        logger.info(f"Offer campaign {campaign_id} completed: {sent} sent, {failed} failed")
    except Exception as e:
        logger.error(f"Campaign {campaign_id} crashed: {e}\n{traceback.format_exc()}")
        try:
            update_campaign(campaign_id, status="failed", error_message=str(e)[:200])
        except Exception:
            pass


def _send_offer_email_with_attachment(smtp_sender, to_addr, subject, html_body, text_body,
                                       pdf_path, pool, campaign_id, sequence_num=None, total=None):
    """Send an email with a PDF attachment, using the SMTPSender directly."""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = f"Gayatri Education HR Team <{smtp_sender.email}>"
            msg["To"] = to_addr
            msg["Reply-To"] = "contactus@gayatrieducation.tech"
            msg["Subject"] = subject
            msg["MIME-Version"] = "1.0"
            msg["X-Mailer"] = "Gayatri Education Bot"
            msg["X-Priority"] = "3"
            msg["X-MSMail-Priority"] = "Normal"
            msg["Importance"] = "Normal"
            msg["List-Unsubscribe"] = "<mailto:contactus@gayatrieducation.tech?subject=unsubscribe>"

            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(text_body, "plain", "utf-8"))
            alt.attach(MIMEText(html_body, "html", "utf-8"))
            msg.attach(alt)

            # Attach PDF
            if pdf_path and os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    part = MIMEBase("application", "pdf")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                safe_name = os.path.basename(pdf_path)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{safe_name}",
                )
                msg.attach(part)

            # Send
            context = create_default_context()
            if smtp_sender.port == 465:
                with smtplib.SMTP_SSL(smtp_sender.server, smtp_sender.port, context=context) as server:
                    server.login(smtp_sender.email, smtp_sender.password)
                    server.sendmail(smtp_sender.email, to_addr, msg.as_string())
            else:
                with smtplib.SMTP(smtp_sender.server, smtp_sender.port) as server:
                    if smtp_sender.use_tls:
                        server.starttls(context=context)
                    server.login(smtp_sender.email, smtp_sender.password)
                    server.sendmail(smtp_sender.email, to_addr, msg.as_string())

            pool.increment(smtp_sender.email)
            try:
                increment_campaign_sent(campaign_id)
            except Exception as e:
                logger.warning(f"Failed to increment campaign sent: {e}")
            try:
                log_email_send(
                    to_email=to_addr,
                    from_email=smtp_sender.email,
                    subject=subject,
                    body_preview=text_body[:200],
                    phase="offer_letter",
                    campaign_id=campaign_id,
                    status="sent",
                )
            except Exception as e:
                logger.warning(f"Failed to log offer send for {to_addr}: {e}")
            progress_str = f"[{sequence_num}/{total}] " if sequence_num and total else ""
            logger.info(f"{progress_str}Sent offer to {to_addr} via {smtp_sender.email}")
            return True

        except smtplib.SMTPSenderRefused as e:
            logger.error(f"Permanent rejection for {to_addr}: {e}")
            log_email_send(
                to_email=to_addr,
                from_email=smtp_sender.email,
                subject=subject,
                body_preview=text_body[:200],
                phase="offer_letter",
                campaign_id=campaign_id,
                status="failed",
                error_message=f"sender_refused: {e}",
            )
            return False
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Auth failed for sender {smtp_sender.email}: {e}")
            log_email_send(
                to_email=to_addr,
                from_email=smtp_sender.email,
                subject=subject,
                body_preview=text_body[:200],
                phase="offer_letter",
                campaign_id=campaign_id,
                status="failed",
                error_message=f"auth_failed: {e}",
            )
            return False
        except smtplib.SMTPRecipientsRefused as e:
            logger.error(f"Recipient refused for {to_addr}: {e}")
            log_email_send(
                to_email=to_addr,
                from_email=smtp_sender.email,
                subject=subject,
                body_preview=text_body[:200],
                phase="offer_letter",
                campaign_id=campaign_id,
                status="failed",
                error_message=f"recipient_refused: {e}",
            )
            return False
        except Exception as e:
            logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed for {to_addr}: {e}")
            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                time.sleep(backoff)

    # All retries exhausted
    log_email_send(
        to_email=to_addr,
        from_email=smtp_sender.email,
        subject=subject,
        body_preview=text_body[:200],
        phase="offer_letter",
        campaign_id=campaign_id,
        status="failed",
        error_message="SMTP send failed after max retries",
    )
    logger.error(f"FAILED to send offer to {to_addr} after {MAX_RETRIES} attempts")
    return False


# ============================================================
# PUBLIC API
# ============================================================

def get_offer_campaign_live_stats(campaign_id):
    """Return live stats for dashboard display."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        return None

    pool_stats = get_all_sender_stats()
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
