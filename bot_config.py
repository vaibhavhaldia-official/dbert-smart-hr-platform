"""
bot_config.py — Non-secret configuration and email content.

Phase 1 (AC-5): every credential, identifier and deployment-specific value has
moved to config.py, which reads them from the environment. This module now holds
only non-secret content — email templates, Google Sheet column names and the
default skill requirements — and re-exports the rest so that the nine modules
importing from `bot_config` keep working unchanged.

Nothing in this file may contain a password, API key, or spreadsheet id.
Set those in .env (see .env.example) or in the production environment.
"""

# Re-export environment-backed configuration (AC-5, AC-7).
from config import (  # noqa: F401
    BASE_DIR,
    GOOGLE_SHEET_NAME,
    GOOGLE_SHEET_ID,
    GOOGLE_SHEET_GID,
    SERVICE_ACCOUNT_KEY,
    POLL_INTERVAL_SECONDS,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_DISPLAY_NAME,
    SMTP_MIN_DELAY_SECONDS,
    SMTP_MAX_DELAY_SECONDS,
    RATE_LIMIT_PER_SENDER_PER_HOUR,
    RATE_LIMIT_PER_SENDER_PER_DAY,
    DEFAULT_REPLY_TO,
    SENDER_POOL,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    LOG_FILE,
    DAILY_SUMMARY_RECIPIENT,
    DATABASE_FILE,
    DB_TIMEOUT_SECONDS,
    DB_BUSY_TIMEOUT_MS,
    FLASK_HOST,
    FLASK_PORT,
    FLASK_DEBUG,
    FLASK_SECRET_KEY,
    OUTPUT_DIR,
    UPLOAD_FOLDER,
    ENROLLMENT_CSV,
    SELECTED_CSV,
    SAMPLE_OFFER_DOCX,
    TEST_EMAIL_RECIPIENT,
)

from datetime import datetime, timedelta


def _next_monday(dt=None):
    dt = dt or datetime.now()
    days_ahead = (7 - dt.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (dt + timedelta(days=days_ahead)).strftime("%d %b %Y")


HR_INDUCTION_DATE = _next_monday()

# ============================================================
# COLUMN NAMES IN GOOGLE SHEET
# ============================================================
COL_EMAIL = "Email Address"
COL_NAME = "Name"
COL_INTERNSHIP = "Select Internship"
COL_COLLEGE = "College Name"
COL_SEMESTER = "Semester"
COL_WHATSAPP = "WhatsApp number"
COL_AVAILABLE = "Are you available for next 2 months for Internship?"
COL_LAPTOP = "Do you Laptop and Internet Connection ?"
COL_PLACEMENT = "Do u require job placement after completion of Internship?"
COL_CV = "Please Upload Your CV"
COL_HEAR_ABOUT = "How did you hear about us"
COL_APPROVAL_STATUS = "Approval Status"
COL_CONFIRMATION_SENT = "Confirmation Sent"

# ============================================================
# EMAIL CONTENT — public links, no secrets
# ============================================================
WHATSAPP_GROUP_URL = "https://chat.whatsapp.com/HiKGjnjetq65jHi9UGczYs"
RESULTS_URL = "https://gayatrieducation.tech/results"
APPLICATION_URL = "https://docs.google.com/forms/d/e/1FAIpQLScHWe1eoxU52pEzKNGE7PMAQtE2inzRReFibXxg8jDZ-X17KQ/viewform?usp=header"

RESULT_SUBJECT = "Your Application Result — Gayatri Education Project"

# ============================================================
# REMINDER EMAILS
# ============================================================
REMINDER_SUBJECTS = [
    "Reminder: Your Application — Gayatri Education Project",
    "Still Interested? — Gayatri Education Project",
    "Don't Miss Out — Gayatri Education Project",
]

REMINDER_BODY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Follow Up: Your Application</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f5f5f5; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333; line-height: 1.6;">

  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f5f5f5; padding: 30px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e0e0e0;">

          <!-- HEADER -->
          <tr>
            <td style="background-color: #1e3a5f; padding: 28px 40px; text-align: center;">
              <p style="margin: 0; font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: 0.5px;">Gayatri Education Project</p>
              <p style="margin: 6px 0 0; font-size: 13px; color: #90cdf4;">Application Follow-Up</p>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="padding: 36px 40px;">

              <p style="font-size: 16px; color: #1e3a5f; margin: 0 0 18px 0;">Hi {name},</p>

              <p style="font-size: 15px; color: #444444; margin: 0 0 16px 0;">
                We noticed you started your application for the <strong>{domain}</strong> internship program but haven't submitted it yet.
              </p>

              <p style="font-size: 15px; color: #444444; margin: 0 0 28px 0;">
                Your spot is still available. Complete your application now to secure your place in the upcoming batch.
              </p>

              <!-- CTA BUTTON -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0;">
                <tr>
                  <td align="center">
                    <a href="{application_url}" style="display: inline-block; background-color: #2563eb; color: #ffffff; padding: 14px 36px; border-radius: 6px; text-decoration: none; font-size: 15px; font-weight: 600;">Complete Your Application</a>
                  </td>
                </tr>
              </table>

              <!-- HR INDUCTION NOTICE -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0;">
                <tr>
                  <td style="background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 16px 20px; border-radius: 4px;">
                    <p style="margin: 0; font-size: 14px; color: #92400e;">
                      <strong>HR Induction:</strong> All selected interns must attend the HR induction on <strong>{induction_date}</strong>.
                    </p>
                    <p style="margin: 6px 0 0; font-size: 13px; color: #92400e;">
                      The security deposit of &#8377;499 will be collected on the date of joining and is fully refundable if you decide not to continue.
                    </p>
                  </td>
                </tr>
              </table>

              <p style="font-size: 14px; color: #666666; margin: 0 0 16px 0;">
                Have questions? Our team is here to help.
              </p>

              <!-- WHATSAPP BUTTON -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 24px 0;">
                <tr>
                  <td align="center">
                    <a href="{whatsapp_url}" style="display: inline-block; background-color: #25D366; color: #ffffff; padding: 12px 28px; border-radius: 6px; text-decoration: none; font-size: 14px; font-weight: 600;">Join WhatsApp Group</a>
                  </td>
                </tr>
              </table>

              <p style="font-size: 13px; color: #888888; margin: 0;">
                You can also check your results and batch updates here:<br>
                <a href="{results_url}" style="color: #2563eb; text-decoration: none;">{results_url}</a>
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

REMINDER_BODY_TEXT = """\
Hi {name},

We noticed you started your application for the {domain} internship program but haven't submitted it yet.

Your spot is still available. Complete your application now to secure your place in the upcoming batch.

Complete your application: {application_url}

HR INDUCTION: {induction_date}
All selected interns must attend the HR induction on {induction_date}.
The security deposit of Rs. 499 will be collected on the date of joining and is fully refundable if you decide not to continue.

Have questions? Join our WhatsApp group for instant support:
{whatsapp_url}

Check your results and batch updates:
{results_url}

---
Gayatri Education Project
"""

RESULT_BODY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Your Application — Gayatri Education Project</title>
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
              <p style="margin: 6px 0 0; font-size: 13px; color: #90cdf4;">Application Received</p>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="padding: 36px 40px;">

              <p style="font-size: 16px; color: #1e3a5f; margin: 0 0 18px 0;">Hi <strong>{name}</strong>,</p>

              <p style="font-size: 15px; color: #444444; margin: 0 0 24px 0;">
                Thank you for applying to the Gayatri Education Project. We have received your application for the <strong>{domain}</strong> internship program.
              </p>

              <!-- DOMAIN CARD -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 28px 0;">
                <tr>
                  <td style="background-color: #eff6ff; border-left: 4px solid #2563eb; padding: 16px 20px; border-radius: 4px;">
                    <p style="margin: 0; font-size: 13px; color: #1e40af; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">Your Selected Track</p>
                    <p style="margin: 4px 0 0; font-size: 17px; color: #1e3a5f; font-weight: 700;">{domain}</p>
                  </td>
                </tr>
              </table>

              <!-- WHAT'S NEXT -->
              <p style="font-size: 16px; font-weight: 700; color: #1e3a5f; margin: 0 0 14px 0;">What happens next?</p>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 28px 0;">
                <tr>
                  <td style="padding: 10px 0; font-size: 14px; color: #444444; border-bottom: 1px solid #f0f0f0;">
                    1. <strong>Join our WhatsApp group</strong> for updates, announcements, and batch schedules.
                  </td>
                </tr>
                <tr>
                  <td style="padding: 10px 0; font-size: 14px; color: #444444; border-bottom: 1px solid #f0f0f0;">
                    2. <strong>Check the results page</strong> regularly for orientation details and enrollment instructions.
                  </td>
                </tr>
                <tr>
                  <td style="padding: 10px 0; font-size: 14px; color: #444444; border-bottom: 1px solid #f0f0f0;">
                    3. <strong>Complete enrollment</strong> to secure your spot in the upcoming batch.
                  </td>
                </tr>
                <tr>
                  <td style="padding: 10px 0; font-size: 14px; color: #444444;">
                    4. <strong>Start your 2-month internship</strong> with real projects and hands-on experience.
                  </td>
                </tr>
              </table>

              <!-- HR INDUCTION NOTICE -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 0 0 28px 0;">
                <tr>
                  <td style="background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 16px 20px; border-radius: 4px;">
                    <p style="margin: 0; font-size: 14px; color: #92400e;">
                      <strong>HR Induction:</strong> All selected interns must attend the HR induction on <strong>{induction_date}</strong>.
                    </p>
                    <p style="margin: 6px 0 0; font-size: 13px; color: #92400e;">
                      The security deposit of &#8377;499 will be collected on the date of joining and is fully refundable if you decide not to continue.
                    </p>
                  </td>
                </tr>
              </table>

              <!-- CTA BUTTONS (dynamically rendered by bot.py) -->
              {cta_buttons}

              <!-- RESULTS LINK -->
              <p style="font-size: 14px; color: #666666; margin: 24px 0 0 0;">
                Check updates and announcements: <a href="{results_url}" style="color: #2563eb; text-decoration: none; font-weight: 600;">{results_url}</a>
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

RESULT_BODY_TEXT = """\
Hi {name},

Thank you for applying to the Gayatri Education Project. We have received your application for the {domain} internship program.

--- YOUR SELECTED TRACK ---
{domain}

--- WHAT'S NEXT ---
1. Join our WhatsApp group for updates and announcements: {whatsapp_url}
2. Check the results page for batch schedules and orientation: {results_url}
3. Complete enrollment to secure your spot: {application_url}
4. Start your 2-month intensive internship with real projects.

Complete enrollment form: {application_url}

HR INDUCTION: {induction_date}
All selected interns must attend the HR induction on {induction_date}.
The security deposit of Rs. 499 will be collected on the date of joining and is fully refundable if you decide not to continue.

Have questions? Reply to this email or join our WhatsApp group. We will get back to you within 24 hours.

Best regards,
Team Gayatri Education
contactus@dbert.online
"""

# ============================================================
# DEFAULT SKILL REQUIREMENTS
# ============================================================
DEFAULT_SKILL_REQUIREMENTS = {
    "Data Analyst": {
        "required_skills": [
            "Excel", "SQL", "Python", "Pandas", "Data Visualization",
            "Power BI", "Statistics", "Data Cleaning", "Analytical Thinking"
        ],
        "description": "Analyze data, create reports, and derive business insights using tools like Excel, SQL, Python, and Power BI.",
        "certifications": [
            "Google Data Analytics Professional Certificate",
            "Microsoft Power BI Data Analyst",
            "IBM Data Analyst Professional Certificate",
        ],
    },
    "Full Stack Development": {
        "required_skills": [
            "HTML", "CSS", "JavaScript", "React", "Python",
            "Flask", "SQL", "Authentication", "REST APIs", "Git"
        ],
        "description": "Build complete web applications from frontend to backend using modern frameworks and databases.",
        "certifications": [
            "Meta Full-Stack Developer Professional Certificate",
            "freeCodeCamp Full Stack Certification",
            "AWS Certified Developer",
        ],
    },
    "AI Agent Development": {
        "required_skills": [
            "LLMs", "Prompt Engineering", "LangChain", "Python",
            "APIs", "RAG", "AI Agents", "Vector Databases"
        ],
        "description": "Develop intelligent AI agents and automation workflows using LLMs, RAG, and agent frameworks.",
        "certifications": [
            "DeepLearning.AI LLM Engineering",
            "LangChain Certification",
            "Hugging Face NLP Engineer",
        ],
    },
    "Python Automation": {
        "required_skills": [
            "Python", "Automation", "Scripting", "Excel Automation",
            "APIs", "File Handling", "Task Scheduling", "Error Handling"
        ],
        "description": "Build Python scripts and automation tools to streamline repetitive tasks, file operations, and integrations.",
        "certifications": [
            "Google IT Automation with Python",
            "PCEP Certified Entry-Level Python Programmer",
            "Microsoft Python Certification",
        ],
    },
}
