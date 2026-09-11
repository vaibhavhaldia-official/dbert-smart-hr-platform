"""
Flask admin dashboard for the Gayatri Education Project.
Provides a local web interface for managing applicants, skills, and email campaigns.

Access at: http://localhost:5000
"""

import csv
import io
import os
import random
import sys
import json
import re
import sqlite3
import logging
import threading
import atexit
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from send_control import (
    is_globally_paused, set_globally_paused, get_global_pause_info,
    is_bot_paused, set_bot_paused, get_bot_pause_info,
    is_campaigns_paused, set_campaigns_paused, get_campaigns_pause_info,
    is_offers_paused, set_offers_paused, get_offers_pause_info,
    get_all_status,
)
from smtp_service import SenderPool, send_and_log

from database import (
    init_database, get_dashboard_stats, get_all_applicants,
    get_applicant_detail, update_approval_status, get_all_skill_requirements,
    save_skill_requirements, delete_skill_requirement, add_processing_log,
    get_master_csv_data, import_master_csv,
    create_campaign, get_campaign, get_active_campaign, get_active_offer_campaign,
    update_campaign,
    get_campaign_stats, get_campaign_send_count,
    get_reminder_campaign_targets, get_all_sender_stats,
    get_offer_letter_targets,
    cleanup_old_sender_stats,
    log_email_send, get_email_log, get_email_log_stats,
    get_all_ctas, get_cta_by_id, create_cta, update_cta, delete_cta,
    upsert_applicant,
)
from bot_config import (
    DATABASE_FILE, FLASK_HOST, FLASK_PORT, FLASK_DEBUG,
    DEFAULT_SKILL_REQUIREMENTS,
    RATE_LIMIT_PER_SENDER_PER_HOUR, SENDER_POOL,
    APPLICATION_URL, WHATSAPP_GROUP_URL,
)

app = Flask(__name__)
app.secret_key = "gayatri-edu-dashboard-2026"
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
init_database()

# ============================================================
# HELPERS
# ============================================================

def get_db():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# GOOGLE API — module-level import with fallback
# ============================================================

try:
    from googleapiclient.discovery import build as _google_build
    from google.oauth2.service_account import Credentials as _GoogleCredentials
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False


# ============================================================
# LOGGING — cleanup handler on shutdown
# ============================================================

_log_file_handler = logging.FileHandler("flask_dashboard.log", encoding="utf-8")
_log_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
root_logger = logging.getLogger()
root_logger.addHandler(_log_file_handler)
root_logger.setLevel(logging.INFO)
atexit.register(lambda: root_logger.removeHandler(_log_file_handler))


def format_date(date_str):
    if not date_str:
        return "—"
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d %b %Y, %I:%M %p")
    except (ValueError, TypeError):
        return str(date_str) if date_str else "—"


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def dashboard():
    stats = get_dashboard_stats()
    stats.update(get_email_log_stats())

    # Campaign context for dashboard
    active_campaign = get_active_campaign()
    sender_stats_list = []
    if active_campaign:
        raw = get_all_sender_stats()
        sender_stats_list = [
            {
                "email": s["email"],
                "name": s.get("name", ""),
                "sent": raw.get(s["email"], 0),
                "limit": RATE_LIMIT_PER_SENDER_PER_HOUR,
                "remaining": max(0, RATE_LIMIT_PER_SENDER_PER_HOUR - raw.get(s["email"], 0)),
            }
            for s in SENDER_POOL
        ]

    # Offer letter campaign context
    offer_active_campaign = get_active_offer_campaign()
    offer_sender_stats_list = []
    if offer_active_campaign:
        raw = get_all_sender_stats()
        offer_sender_stats_list = [
            {
                "email": s["email"],
                "name": s.get("name", ""),
                "sent": raw.get(s["email"], 0),
                "limit": RATE_LIMIT_PER_SENDER_PER_HOUR,
                "remaining": max(0, RATE_LIMIT_PER_SENDER_PER_HOUR - raw.get(s["email"], 0)),
            }
            for s in SENDER_POOL
        ]

    from database import get_email_analytics
    email_stats = get_email_analytics()

    return render_template(
        "dashboard.html",
        stats=stats,
        email_stats=email_stats,
        format_date=format_date,
        active_campaign=active_campaign,
        sender_stats=sender_stats_list,
        rate_limit_per_hour=RATE_LIMIT_PER_SENDER_PER_HOUR,
        offer_active_campaign=offer_active_campaign,
        offer_sender_stats=offer_sender_stats_list,
        global_send_paused=is_globally_paused(),
        global_pause_since=get_global_pause_info()[1],
        bot_paused=is_bot_paused(),
        bot_pause_since=get_bot_pause_info()[1],
        campaigns_paused=is_campaigns_paused(),
        campaigns_pause_since=get_campaigns_pause_info()[1],
        offers_paused=is_offers_paused(),
        offers_pause_since=get_offers_pause_info()[1],
    )


@app.route("/applicants")
def applicants():
    domain_filter = request.args.get("domain", "")
    status_filter = request.args.get("status", "")
    search = request.args.get("search", "").strip()

    filters = {}
    if domain_filter:
        filters["domain"] = domain_filter
    if status_filter:
        filters["approval_status"] = status_filter

    all_applicants = get_all_applicants(limit=None, filters=filters)

    # Apply search filter (client-side)
    if search:
        search_lower = search.lower()
        all_applicants = [
            a for a in all_applicants
            if search_lower in (a.get("name", "") or "").lower()
            or search_lower in (a.get("email", "") or "").lower()
        ]

    # Get unique domains for filter dropdown
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT internship_domain FROM applicants WHERE internship_domain IS NOT NULL AND internship_domain != '' ORDER BY internship_domain")
    domains = [r["internship_domain"] for r in cursor.fetchall()]
    conn.close()

    return render_template(
        "applicants.html",
        applicants=all_applicants,
        domains=domains,
        filters={"domain": domain_filter, "status": status_filter, "search": search},
        format_date=format_date,
    )


@app.route("/applicant/<int:applicant_id>")
def applicant_detail(applicant_id):
    applicant = get_applicant_detail(applicant_id)
    if not applicant:
        flash("Applicant not found.", "error")
        return redirect(url_for("applicants"))

    return render_template(
        "applicant_detail.html",
        applicant=applicant,
        format_date=format_date,
    )


@app.route("/applicant/<int:applicant_id>/approve", methods=["POST"])
def approve_applicant(applicant_id):
    action = request.form.get("action")
    status = "Approved" if action == "approve" else "Rejected"

    applicant = get_applicant_detail(applicant_id)
    if not applicant:
        flash("Applicant not found.", "error")
        return redirect(url_for("applicants"))

    email = applicant["email"]

    # Update local DB
    update_approval_status(email, status)
    add_processing_log(email, f"status_changed_{status.lower()}", f"Changed to {status} via dashboard")

    # Write to Google Sheet
    if not _GOOGLE_AVAILABLE:
        flash("Google API libraries not available — sheet update skipped. Local status updated.", "warning")
        return redirect(url_for("applicant_detail", applicant_id=applicant_id))

    try:
        from bot_config import SERVICE_ACCOUNT_KEY, GOOGLE_SHEET_ID

        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_KEY, scopes=scopes)
        service = build("sheets", "v4", credentials=creds)

        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Form Responses 1",
            majorDimension="ROWS"
        ).execute()

        rows = result.get("values", [])
        if rows:
            headers = [h.strip().lower() for h in rows[0]]
            try:
                email_col = headers.index("email address")
                approval_col = headers.index("approval status")
            except ValueError:
                flash("Could not find required columns in Google Sheet.", "error")
                return redirect(url_for("applicant_detail", applicant_id=applicant_id))

            def col_to_letter(col_idx):
                if col_idx < 26:
                    return chr(65 + col_idx)
                result = ""
                while col_idx >= 0:
                    col_idx, remainder = divmod(col_idx, 26)
                    result = chr(65 + remainder) + result
                    col_idx -= 1
                return result

            for i, row in enumerate(rows[1:], start=2):
                if len(row) > email_col and row[email_col].strip().lower() == email.lower():
                    col_letter = col_to_letter(approval_col)
                    service.spreadsheets().values().update(
                        spreadsheetId=GOOGLE_SHEET_ID,
                        range=f"Form Responses 1!{col_letter}{i}",
                        valueInputOption="RAW",
                        body={"values": [[status]]}
                    ).execute()
                    add_processing_log(email, "sheet_updated", f"Approval status set to {status} in sheet")
                    flash(f"Applicant marked as {status}. Google Sheet updated.", "success")
                    break
            else:
                flash(f"Applicant found in DB but not in Google Sheet (email: {email}). Local status updated only.", "warning")
        else:
            flash("Google Sheet is empty.", "warning")

    except Exception as e:
        flash(f"Local status updated, but Google Sheet update failed: {e}", "warning")

    return redirect(url_for("applicant_detail", applicant_id=applicant_id))


@app.route("/skills")
def skill_management():
    skills = get_all_skill_requirements()
    # Parse JSON fields for template rendering
    for s in skills:
        try:
            s["required_skills_list"] = json.loads(s["required_skills"]) if s["required_skills"] else []
        except (json.JSONDecodeError, TypeError):
            s["required_skills_list"] = []
        try:
            s["certifications_list"] = json.loads(s["certifications"]) if s.get("certifications") else []
        except (json.JSONDecodeError, TypeError):
            s["certifications_list"] = []
    return render_template("skills.html", skills=skills, format_date=format_date)


@app.route("/skills/generate", methods=["POST"])
def generate_skills():
    flash("Auto-generation requires an LLM which is no longer available. Please add skills manually.", "warning")
    return redirect(url_for("skill_management"))


@app.route("/skills/save", methods=["POST"])
def save_skills():
    domain = request.form.get("domain", "").strip()
    skills_raw = request.form.get("skills", "").strip()
    description = request.form.get("description", "").strip()
    certifications_raw = request.form.get("certifications", "").strip()

    if not domain or not skills_raw:
        flash("Domain and skills are required.", "error")
        return redirect(url_for("skill_management"))

    skills_list = [s.strip() for s in skills_raw.split(",") if s.strip()]
    cert_list = [s.strip() for s in certifications_raw.split(",") if s.strip()] if certifications_raw else None

    if not skills_list:
        flash("No valid skills found.", "error")
        return redirect(url_for("skill_management"))

    save_skill_requirements(domain, skills_list, description, source="manual", certifications=cert_list)
    flash(f"Skills for '{domain}' updated ({len(skills_list)} skills, {len(cert_list) if cert_list else 0} certifications).", "success")
    return redirect(url_for("skill_management"))


@app.route("/skills/delete", methods=["POST"])
def delete_skills():
    domain = request.form.get("domain", "").strip()
    if domain:
        delete_skill_requirement(domain)
        flash(f"Deleted skill requirements for '{domain}'.", "success")
    return redirect(url_for("skill_management"))


@app.route("/settings")
def settings():
    from bot_config import DATABASE_FILE, SENDER_POOL, GOOGLE_SHEET_NAME
    return render_template("settings.html",
        DATABASE_FILE=DATABASE_FILE,
        sender_pool_count=len(SENDER_POOL),
        sheet_name=GOOGLE_SHEET_NAME,
    )


@app.route("/api/stats")
def api_stats():
    stats = get_dashboard_stats()
    return jsonify(stats)


@app.route("/api/sync_sheet", methods=["POST"])
def sync_google_sheet():
    """Pull latest data from Google Sheet into local DB."""
    if not _GOOGLE_AVAILABLE:
        return jsonify({"status": "error", "message": "Google API libraries not available"}), 503

    try:
        from bot_config import SERVICE_ACCOUNT_KEY, GOOGLE_SHEET_ID, GOOGLE_SHEET_GID

        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_KEY, scopes=scopes)
        service = build("sheets", "v4", credentials=creds)

        # Resolve sheet tab name from GID
        meta = service.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID).execute()
        sheet_tab = None
        for s in meta.get("sheets", []):
            if str(s.get("properties", {}).get("sheetId", "")) == str(GOGLE_SHEET_GID):
                sheet_tab = s["properties"]["title"]
                break
        if not sheet_tab:
            sheet_tab = meta.get("sheets", [{}])[0].get("properties", {}).get("title", "Sheet1")

        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=sheet_tab,
            majorDimension="ROWS"
        ).execute()

        rows = result.get("values", [])
        if not rows:
            return jsonify({"status": "error", "message": "Sheet is empty"})

        headers = [h.strip() for h in rows[0]]
        count = 0

        for row in rows[1:]:
            while len(row) < len(headers):
                row.append("")

            data = dict(zip(headers, row))

            email = data.get("Email Address", "").strip()
            if not email:
                continue

            applicant_data = {
                "email": email,
                "name": data.get("Name", ""),
                "internship": data.get("Select Internship", ""),
                "college": data.get("College Name", ""),
                "semester": data.get("Semester", ""),
                "whatsapp": data.get("WhatsApp number", ""),
                "available": data.get("Are you available for next 2 months for Internship?", ""),
                "laptop": data.get("Do you Laptop and Internet Connection ?", ""),
                "placement": data.get("Do u require job placement after completion of Internship?", ""),
                "cv": data.get("Please Upload Your CV", ""),
                "hear_about": data.get("How did you hear about us", ""),
                "approval_status": data.get("Approval Status", "Pending"),
                "email_sent_at": data.get("Email Sent", ""),
                "confirmation_sent_at": data.get("Confirmation Sent", ""),
                "timestamp": data.get("Timestamp", ""),
            }

            upsert_applicant(applicant_data)
            count += 1

        return jsonify({"status": "ok", "synced": count, "message": f"Synced {count} applicants from Google Sheet"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/import/csv", methods=["POST"])
def import_csv():
    """Upload a master CSV to replace all database data."""
    if "csv_file" not in request.files:
        flash("No file selected.", "error")
        return redirect(url_for("dashboard"))

    f = request.files["csv_file"]
    if f.filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("dashboard"))

    if not f.filename.lower().endswith(".csv"):
        flash("Please upload a .csv file.", "error")
        return redirect(url_for("dashboard"))

    tmp = os.path.join(app.config["UPLOAD_FOLDER"], "_import_master.csv")
    f.save(tmp)

    result = import_master_csv(tmp)
    os.remove(tmp)

    if result.get("status") == "error":
        flash(f"Import failed: {result['message']}", "error")
    else:
        msg = (f"Import complete — {result['total']} rows read, "
               f"{result['inserted']} inserted, "
               f"{result['skipped']} skipped")
        if result["errors"]:
            msg += f", {len(result['errors'])} errors (check logs)"
            for e in result["errors"][:5]:
                flash(e, "warning")
        flash(msg, "success")

    return redirect(url_for("dashboard"))


@app.route("/download/master")
def download_master_csv():
    """Export all applicant data as a master CSV file."""
    rows = get_master_csv_data()

    fieldnames = [
        ("id", "ID"),
        ("email", "Email"),
        ("name", "Name"),
        ("internship_domain", "Internship Domain"),
        ("college", "College"),
        ("semester", "Semester"),
        ("whatsapp", "WhatsApp"),
        ("available_2months", "Available 2 Months"),
        ("has_laptop_internet", "Has Laptop & Internet"),
        ("needs_placement", "Needs Placement"),
        ("cv_file_link", "CV File Link"),
        ("how_did_you_hear", "How Did You Hear"),
        ("approval_status", "Approval Status"),
        ("email_sent_at", "Acknowledgement Sent At"),
        ("confirmation_sent_at", "Result Email Sent At"),
        ("submitted_at", "Submitted At"),
        ("created_at", "Created At"),
        ("updated_at", "Updated At"),
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[f[1] for f in fieldnames])
    writer.writeheader()
    for row in rows:
        writer.writerow({display_name: row.get(key, "") for key, display_name in fieldnames})

    output.seek(0)
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"gayatri_master_{today}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============================================================
# BULK EMAIL CAMPAIGN ROUTES
# ============================================================

_campaign_threads = {}


@app.route("/api/campaign/eligible-count", methods=["GET"])
def api_campaign_eligible_count():
    """Return count of applicants eligible for the reminder campaign."""
    targets = get_reminder_campaign_targets()
    return jsonify({
        "eligible": len(targets),
        "domains": sorted({t.get("internship_domain", "unknown") for t in targets}),
    })


@app.route("/campaign/start", methods=["POST"])
def campaign_start():
    """Create a new reminder campaign and launch it in a background thread."""
    active = get_active_campaign()
    if active:
        return jsonify({"error": f"Campaign {active['id']} already active", "campaign_id": active["id"]}), 400

    campaign_name = request.form.get("campaign_name", "Reminder Campaign")
    campaign_id = create_campaign(campaign_name, phase="reminder")

    from smtp_sender import run_campaign
    thread = threading.Thread(target=run_campaign, args=(campaign_id,), daemon=True)
    _campaign_threads[campaign_id] = thread
    thread.start()

    return jsonify({"ok": True, "campaign_id": campaign_id, "status": "running"})


@app.route("/campaign/<int:campaign_id>/pause", methods=["POST"])
def campaign_pause(campaign_id):
    update_campaign(campaign_id, status="paused")
    return jsonify({"ok": True, "campaign_id": campaign_id, "status": "paused"})


@app.route("/campaign/<int:campaign_id>/resume", methods=["POST"])
def campaign_resume(campaign_id):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({"error": "Campaign not found"}), 404

    if campaign["status"] == "running":
        return jsonify({"ok": True, "campaign_id": campaign_id, "status": "running", "note": "Already running"})

    update_campaign(campaign_id, status="running")
    from smtp_sender import run_campaign
    thread = threading.Thread(target=run_campaign, args=(campaign_id,), daemon=True)
    _campaign_threads[campaign_id] = thread
    thread.start()

    return jsonify({"ok": True, "campaign_id": campaign_id, "status": "running"})


@app.route("/campaign/<int:campaign_id>/status", methods=["GET"])
def campaign_status(campaign_id):
    """Return live status JSON for a campaign."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({"error": "Campaign not found"}), 404

    stats = get_campaign_stats(campaign_id)
    sender_stats = get_all_sender_stats()

    sender_view = []
    for s in SENDER_POOL:
        sent = sender_stats.get(s["email"], 0)
        sender_view.append({
            "email": s["email"],
            "name": s.get("name", ""),
            "sent": sent,
            "limit": RATE_LIMIT_PER_SENDER_PER_HOUR,
            "remaining": max(0, RATE_LIMIT_PER_SENDER_PER_HOUR - sent),
        })

    return jsonify({
        "campaign": campaign,
        "stats": stats,
        "senders": sender_view,
        "thread_alive": campaign_id in _campaign_threads and _campaign_threads[campaign_id].is_alive(),
    })


@app.route("/api/email-log")
def api_email_log():
    """Return recent email_log entries for the dashboard."""
    limit = request.args.get("limit", 100, type=int)
    to_email = request.args.get("to_email", "")
    status = request.args.get("status", "")
    bounce_only = request.args.get("bounce_only", "0") == "1"
    rows = get_email_log(limit=limit, to_email=to_email, status=status, bounce_only=bounce_only)
    return jsonify(rows)


@app.route("/email-log")
def email_log_page():
    """Email log viewer page."""
    return render_template("email_log.html", format_date=format_date)


@app.route("/ctas")
def cta_management():
    ctas = get_all_ctas()
    return render_template("ctas.html", ctas=ctas, format_date=format_date)


@app.route("/ctas/new", methods=["POST"])
def cta_create():
    label = request.form.get("label", "").strip()
    url = request.form.get("url", "").strip()
    icon = request.form.get("icon", "").strip()
    bg_color = request.form.get("bg_color", "#2563eb").strip()
    text_color = request.form.get("text_color", "#ffffff").strip()
    email_type = request.form.get("email_type", "result").strip()
    sort_order = request.form.get("sort_order", "0", type=int) or 0

    if not label or not url:
        flash("Label and URL are required.", "error")
    else:
        create_cta(label, url, icon=icon, bg_color=bg_color, text_color=text_color,
                   email_type=email_type, sort_order=sort_order)
        flash(f"CTA '{label}' created.", "success")
    return redirect(url_for("cta_management"))


@app.route("/ctas/<int:cta_id>/edit", methods=["POST"])
def cta_edit(cta_id):
    cta = get_cta_by_id(cta_id)
    if not cta:
        flash("CTA not found.", "error")
        return redirect(url_for("cta_management"))

    update_cta(cta_id,
        label=request.form.get("label", "").strip(),
        url=request.form.get("url", "").strip(),
        icon=request.form.get("icon", "").strip(),
        bg_color=request.form.get("bg_color", "#2563eb").strip(),
        text_color=request.form.get("text_color", "#ffffff").strip(),
        email_type=request.form.get("email_type", "result").strip(),
        sort_order=request.form.get("sort_order", "0", type=int),
    )
    flash("CTA updated.", "success")
    return redirect(url_for("cta_management"))


@app.route("/ctas/<int:cta_id>/delete", methods=["POST"])
def cta_delete(cta_id):
    cta = get_cta_by_id(cta_id)
    if cta:
        delete_cta(cta_id)
        flash(f"CTA '{cta['label']}' deleted.", "success")
    return redirect(url_for("cta_management"))


@app.route("/campaign/active", methods=["GET"])
def campaign_active():
    active = get_active_campaign()
    if not active:
        return jsonify({"active": False})
    return jsonify({"active": True, "campaign": active})


# ============================================================
# OFFER LETTER CAMPAIGN ROUTES
# ============================================================

@app.route("/api/offer-letters/eligible-count", methods=["GET"])
def api_offer_eligible_count():
    """Return count of applicants eligible for offer letter sending."""
    try:
        targets = get_offer_letter_targets()
    except Exception as e:
        root_logger.error(f"Failed to get offer letter targets: {e}")
        targets = []
    return jsonify({
        "eligible": len(targets),
    })


@app.route("/offer-letters/start", methods=["POST"])
def offer_letters_start():
    """Create a new offer letter campaign and launch it in a background thread."""
    active = get_active_offer_campaign()
    if active:
        return jsonify({"error": f"Campaign {active['id']} already active", "campaign_id": active["id"]}), 400

    campaign_name = request.form.get("campaign_name", "Offer Letter Campaign")
    campaign_id = create_campaign(campaign_name, phase="offer_letter")

    from offer_letter_sender import run_offer_campaign
    thread = threading.Thread(target=run_offer_campaign, args=(campaign_id,), daemon=True)
    _campaign_threads[campaign_id] = thread
    thread.start()

    return jsonify({"ok": True, "campaign_id": campaign_id, "status": "running"})


@app.route("/offer-letters/<int:campaign_id>/pause", methods=["POST"])
def offer_letters_pause(campaign_id):
    update_campaign(campaign_id, status="paused")
    return jsonify({"ok": True, "campaign_id": campaign_id, "status": "paused"})


@app.route("/offer-letters/<int:campaign_id>/resume", methods=["POST"])
def offer_letters_resume(campaign_id):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({"error": "Campaign not found"}), 404

    if campaign["status"] == "running":
        return jsonify({"ok": True, "campaign_id": campaign_id, "status": "running", "note": "Already running"})

    update_campaign(campaign_id, status="running")

    from offer_letter_sender import run_offer_campaign
    thread = threading.Thread(target=run_offer_campaign, args=(campaign_id,), daemon=True)
    _campaign_threads[campaign_id] = thread
    thread.start()

    return jsonify({"ok": True, "campaign_id": campaign_id, "status": "running"})


@app.route("/offer-letters/<int:campaign_id>/status", methods=["GET"])
def offer_letters_status(campaign_id):
    """Return live status JSON for an offer letter campaign."""
    from offer_letter_sender import get_offer_campaign_live_stats
    stats = get_offer_campaign_live_stats(campaign_id)
    if not stats:
        return jsonify({"error": "Campaign not found"}), 404

    # Get recent offer_letter email log entries for per-recipient tracking
    log_rows = get_email_log(limit=200, campaign_id=campaign_id)
    recent_sends = [
        {
            "to_email": r.get("to_email", ""),
            "status": r.get("status", ""),
            "subject": r.get("subject", ""),
            "sent_at": r.get("sent_at", ""),
            "error_message": r.get("error_message", ""),
            "delivery_status": r.get("delivery_status", ""),
        }
        for r in log_rows[:50]
    ]

    return jsonify({
        "campaign": stats["campaign"],
        "sender_stats": stats["sender_stats"],
        "limit_per_sender": stats["limit_per_sender"],
        "thread_alive": campaign_id in _campaign_threads and _campaign_threads[campaign_id].is_alive(),
        "recent_sends": recent_sends,
    })


@app.route("/api/offer-letters/template", methods=["GET"])
def api_offer_template():
    """Return the offer letter email template with sample data for preview."""
    from offer_letter_sender import (
        OFFER_BODY_HTML, OFFER_BODY_TEXT, OFFER_SUBJECTS,
        EMAIL_INTROS, EMAIL_OUTROS, _parse_start_date,
    )
    # Compute next Monday's induction date (same formula as offer_letter_sender.py)
    hr_induction_date = (datetime.now() + timedelta(days=(7 - datetime.now().weekday()) % 7 or 7)).strftime("%d %b %Y")

    sample_name = "Rahul Sharma"
    sample_domain = "Full Stack Development"
    sample_start = _parse_start_date("")
    start_dt = datetime.strptime(sample_start, "%d-%b-%Y")
    sample_end = (start_dt + timedelta(days=60)).strftime("%d-%b-%Y")
    sample_intro = EMAIL_INTROS[0]
    sample_outro = EMAIL_OUTROS[0]
    sample_subject = random.choice(OFFER_SUBJECTS).format(name=sample_name, start_date=sample_start)

    html_preview = OFFER_BODY_HTML.format(
        name=sample_name, intro=sample_intro, domain=sample_domain,
        start_date=sample_start, end_date=sample_end,
        induction_date=hr_induction_date,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        work_mode="Fully Remote (Work from Home)",
        hours_per_week="10",
        stipend_fixed="5,000", stipend_performance="13,000", stipend_total="18,000",
        outro=sample_outro,
    )
    text_preview = OFFER_BODY_TEXT.format(
        name=sample_name, intro=sample_intro, domain=sample_domain,
        start_date=sample_start, end_date=sample_end,
        induction_date=hr_induction_date,
        application_url=APPLICATION_URL,
        whatsapp_url=WHATSAPP_GROUP_URL,
        work_mode="Fully Remote (Work from Home)",
        hours_per_week="10",
        stipend_fixed="5,000", stipend_performance="13,000", stipend_total="18,000",
        outro=sample_outro,
    )

    return jsonify({
        "subject": sample_subject,
        "reply_to": "contactus@gayatrieducation.tech",
        "html": html_preview,
        "text": text_preview,
    })


# ============================================================
# TEST EMAIL — send a rendered template to the admin's inbox
# ============================================================

TEST_EMAIL_RECIPIENT = "ain.sharma.abhinav@gmail.com"


@app.route("/api/send-test-email", methods=["POST"])
def api_send_test_email():
    """Render a template with sample data and send it to the admin test address.

    Accepts JSON body: { "template": "result" | "reminder" | "offer_letter" }
    Uses the shared SenderPool so rate-limits and retries apply normally.
    Logged with phase="test" so it's distinguishable from real sends.
    """
    data = request.get_json(silent=True) or {}
    template_type = data.get("template", "")

    # --- Validate ---
    if template_type not in ("result", "reminder", "offer_letter"):
        return jsonify({"error": "Invalid template type. Use: result, reminder, or offer_letter"}), 400

    # --- Check if globally paused ---
    if is_globally_paused():
        return jsonify({"error": "Global send is paused. Resume sending first."}), 423

    try:
        pool = SenderPool()
    except Exception as e:
        root_logger.error(f"Failed to create sender pool for test email: {e}")
        return jsonify({"error": "SMTP pool unavailable"}), 503

    # --- Build subject + bodies based on template type ---
    subject = ""
    html_body = ""
    text_body = ""

    if template_type == "result":
        from bot_config import (
            RESULT_SUBJECT, RESULT_BODY_HTML, RESULT_BODY_TEXT,
            APPLICATION_URL, WHATSAPP_GROUP_URL, RESULTS_URL,
            HR_INDUCTION_DATE,
        )
        sample_name = "Abhinav Sharma"
        sample_domain = "Full Stack Development"
        # Fetch dynamic CTAs (safe failure)
        cta_buttons_html = ""
        try:
            from database import get_all_ctas
            cta_list = get_all_ctas(email_type="result") or []
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
                        l = left[i] if i < len(left) else None
                        r = right[i] if i < len(right) else None
                        rows_html += "  <tr>\n"
                        rows_html += '    <td width="48%" style="padding-right: 8px;">\n'
                        if l:
                            rows_html += f'      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center" style="background-color: {l["bg_color"]}; border-radius: 6px; padding: 14px 20px;"><a href="{l["url"]}" style="color: {l["text_color"]}; text-decoration: none; font-size: 15px; font-weight: bold; display: block;">{l.get("icon","")} {l["label"]}</a></td></tr></table>'
                        rows_html += "\n    </td>\n"
                        rows_html += '    <td width="4%">&nbsp;</td>\n'
                        rows_html += '    <td width="48%" style="padding-left: 8px;">\n'
                        if r:
                            rows_html += f'      <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center" style="background-color: {r["bg_color"]}; border-radius: 6px; padding: 14px 20px;"><a href="{r["url"]}" style="color: {r["text_color"]}; text-decoration: none; font-size: 15px; font-weight: bold; display: block;">{r.get("icon","")} {r["label"]}</a></td></tr></table>'
                        rows_html += "\n    </td>\n  </tr>\n"
                    cta_buttons_html = f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin: 28px 0;">\n{rows_html}</table>'
        except Exception:
            pass

        subject = RESULT_SUBJECT
        html_body = RESULT_BODY_HTML.format(
            name=sample_name, domain=sample_domain,
            cta_buttons=cta_buttons_html,
            application_url=APPLICATION_URL,
            whatsapp_url=WHATSAPP_GROUP_URL,
            results_url=RESULTS_URL,
            induction_date=HR_INDUCTION_DATE,
        )
        text_body = RESULT_BODY_TEXT.format(
            name=sample_name, domain=sample_domain,
            application_url=APPLICATION_URL,
            whatsapp_url=WHATSAPP_GROUP_URL,
            results_url=RESULTS_URL,
            induction_date=HR_INDUCTION_DATE,
        )

    elif template_type == "reminder":
        from bot_config import (
            REMINDER_SUBJECTS, REMINDER_BODY_HTML, REMINDER_BODY_TEXT,
            APPLICATION_URL, WHATSAPP_GROUP_URL, RESULTS_URL,
            HR_INDUCTION_DATE,
        )
        import random as _rnd
        sample_name = "Abhinav Sharma"
        sample_domain = "Full Stack Development"
        subject = _rnd.choice(REMINDER_SUBJECTS)
        html_body = REMINDER_BODY_HTML.format(
            name=sample_name, domain=sample_domain,
            application_url=APPLICATION_URL,
            whatsapp_url=WHATSAPP_GROUP_URL,
            results_url=RESULTS_URL,
            induction_date=HR_INDUCTION_DATE,
        )
        text_body = REMINDER_BODY_TEXT.format(
            name=sample_name, domain=sample_domain,
            application_url=APPLICATION_URL,
            whatsapp_url=WHATSAPP_GROUP_URL,
            results_url=RESULTS_URL,
            induction_date=HR_INDUCTION_DATE,
        )

    elif template_type == "offer_letter":
        from bot_config import APPLICATION_URL, WHATSAPP_GROUP_URL
        from offer_letter_sender import (
            OFFER_BODY_HTML, OFFER_BODY_TEXT, OFFER_SUBJECTS,
            EMAIL_INTROS, EMAIL_OUTROS, HR_INDUCTION_DATE,
            STIPEND_FIXED, STIPEND_PERFORMANCE, STIPEND_TOTAL,
            HOURS_PER_WEEK, WORK_MODE,
        )
        from datetime import datetime as _dt, timedelta as _td
        import random as _rnd
        sample_name = "Abhinav Sharma"
        sample_domain = "Full Stack Development"
        start_date_str = "27 Aug 2026"
        end_dt = _dt.strptime(start_date_str, "%d %b %Y") + _td(days=60)
        end_date_str = end_dt.strftime("%d %b %Y")
        intro = _rnd.choice(EMAIL_INTROS)
        outro = _rnd.choice(EMAIL_OUTROS)
        subject = _rnd.choice(OFFER_SUBJECTS).format(name=sample_name, start_date=start_date_str)

        html_body = OFFER_BODY_HTML.format(
            name=sample_name, intro=intro, domain=sample_domain,
            start_date=start_date_str, end_date=end_date_str,
            induction_date=HR_INDUCTION_DATE,
            application_url=APPLICATION_URL,
            whatsapp_url=WHATSAPP_GROUP_URL,
            work_mode=WORK_MODE, hours_per_week=HOURS_PER_WEEK,
            stipend_fixed=STIPEND_FIXED,
            stipend_performance=STIPEND_PERFORMANCE,
            stipend_total=STIPEND_TOTAL,
            outro=outro,
        )
        text_body = OFFER_BODY_TEXT.format(
            name=sample_name, intro=intro, domain=sample_domain,
            start_date=start_date_str, end_date=end_date_str,
            induction_date=HR_INDUCTION_DATE,
            application_url=APPLICATION_URL,
            whatsapp_url=WHATSAPP_GROUP_URL,
            work_mode=WORK_MODE, hours_per_week=HOURS_PER_WEEK,
            stipend_fixed=STIPEND_FIXED,
            stipend_performance=STIPEND_PERFORMANCE,
            stipend_total=STIPEND_TOTAL,
            outro=outro,
        )

    # --- Send ---
    success, sender_email = send_and_log(
        pool, TEST_EMAIL_RECIPIENT, subject, html_body, text_body,
        phase="test",
    )

    if success:
        root_logger.info(f"Test email ({template_type}) sent to {TEST_EMAIL_RECIPIENT} via {sender_email}")
        return jsonify({
            "ok": True,
            "template": template_type,
            "recipient": TEST_EMAIL_RECIPIENT,
            "sender": sender_email,
            "subject": subject,
        })
    else:
        root_logger.error(f"Test email ({template_type}) failed to send to {TEST_EMAIL_RECIPIENT}")
        return jsonify({"error": "Failed to send. All senders may be rate-limited or SMTP is unreachable."}), 503


# ============================================================
# EMAIL ANALYTICS API
# ============================================================

@app.route("/api/email-analytics", methods=["GET"])
def api_email_analytics():
    """Return comprehensive email analytics as JSON for the dashboard."""
    from database import get_email_analytics
    return jsonify(get_email_analytics())


# ============================================================
# PER-SCRIPT SEND CONTROL — pause/resume individual scripts
# ============================================================

@app.route("/api/send-control/bot/pause", methods=["POST"])
def api_bot_pause():
    set_bot_paused(True)
    return jsonify({"ok": True, "script": "bot", "paused": True})


@app.route("/api/send-control/bot/resume", methods=["POST"])
def api_bot_resume():
    set_bot_paused(False)
    return jsonify({"ok": True, "script": "bot", "paused": False})


@app.route("/api/send-control/campaigns/pause", methods=["POST"])
def api_campaigns_pause():
    set_campaigns_paused(True)
    return jsonify({"ok": True, "script": "campaigns", "paused": True})


@app.route("/api/send-control/campaigns/resume", methods=["POST"])
def api_campaigns_resume():
    set_campaigns_paused(False)
    return jsonify({"ok": True, "script": "campaigns", "paused": False})


@app.route("/api/send-control/offers/pause", methods=["POST"])
def api_offers_pause():
    set_offers_paused(True)
    return jsonify({"ok": True, "script": "offers", "paused": True})


@app.route("/api/send-control/offers/resume", methods=["POST"])
def api_offers_resume():
    set_offers_paused(False)
    return jsonify({"ok": True, "script": "offers", "paused": False})


@app.route("/api/send-control/global/pause", methods=["POST"])
def api_global_pause():
    set_globally_paused(True)
    return jsonify({"ok": True, "script": "all", "paused": True})


@app.route("/api/send-control/global/resume", methods=["POST"])
def api_global_resume():
    set_globally_paused(False)
    return jsonify({"ok": True, "script": "all", "paused": False})


@app.route("/api/send-control/pause", methods=["POST"])
def api_global_pause_short():
    set_globally_paused(True)
    return jsonify({"ok": True, "script": "all", "paused": True})


@app.route("/api/send-control/resume", methods=["POST"])
def api_global_resume_short():
    set_globally_paused(False)
    return jsonify({"ok": True, "script": "all", "paused": False})


# ============================================================
# TERMINATE ALL — emergency stop for everything
# ============================================================

@app.route("/api/terminate-all", methods=["POST"])
def api_terminate_all():
    """Emergency stop: pause all scripts, mark campaigns stopped, return summary."""
    import time as _time

    # Set all pause flags — scripts check these in their main loops
    set_globally_paused(True)
    set_bot_paused(True)
    set_campaigns_paused(True)
    set_offers_paused(True)

    # Mark any running campaigns as stopped in DB
    stopped_campaigns = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for cid, thread in list(_campaign_threads.items()):
        if thread.is_alive():
            from database import get_campaign as _gc, update_campaign as _uc
            campaign = _gc(cid)
            if campaign and campaign.get("status") == "running":
                _uc(cid, status="stopped", completed_at=now_str)
                stopped_campaigns.append(cid)

    # Clear thread references (they're daemon threads, will exit after current iteration)
    _campaign_threads.clear()

    # Note: bot.py and offer_letter_sender.py run as separate OS processes
    # launched via launch_hidden.vbs. Setting all pause flags causes them
    # to skip processing on their next loop iteration. A full process kill
    # would require OS-level process management (taskkill) which is best
    # handled manually or via the Windows Task Manager.

    return jsonify({
        "ok": True,
        "terminated": True,
        "paused": {
            "global": True,
            "bot": True,
            "campaigns": True,
            "offers": True,
        },
        "stopped_campaigns": stopped_campaigns,
        "note": "All send systems paused. Bot and offer processes will stop on next loop iteration. Use Task Manager to force-kill if needed.",
    })


@app.route("/api/send-control/status", methods=["GET"])
def api_send_control_status():
    paused, since = get_global_pause_info()
    return jsonify({"paused": paused, "since": since})


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
