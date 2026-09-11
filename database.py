"""
database.py — SQLite database module for the Gayatri Education Project.
Handles all local data storage: applicants, skill requirements, processing log,
email logging, campaign management, CTAs.
"""

import sqlite3
import json
import os
from datetime import datetime
from contextlib import contextmanager

from bot_config import DATABASE_FILE


def get_connection():
    """Create and return a database connection with row factory.

    All callers MUST call conn.close() after use.
    """
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections — ensures conn.close() is always called."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_database():
    """Create all tables if they don't exist, and seed default skill requirements."""
    conn = get_connection()
    cursor = conn.cursor()

    # ============================================================
    # APPLICANTS TABLE
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS applicants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            internship_domain TEXT,
            college TEXT,
            semester TEXT,
            whatsapp TEXT,
            available_2months TEXT,
            has_laptop_internet TEXT,
            needs_placement TEXT,
            cv_file_link TEXT,
            how_did_you_hear TEXT,
            approval_status TEXT DEFAULT 'Pending',
            email_sent_at TEXT,
            confirmation_sent_at TEXT,
            offer_sent_at TEXT,
            submitted_at TEXT,
            upskilling_course TEXT,
            course_name TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # SKILL REQUIREMENTS TABLE
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS skill_requirements (
            domain TEXT PRIMARY KEY,
            required_skills TEXT,
            description TEXT,
            source TEXT DEFAULT 'manual',
            certifications TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # PROCESSING LOG TABLE
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_email TEXT,
            action TEXT,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # EMAIL CAMPAIGN TABLE
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_campaign (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_name TEXT NOT NULL,
            phase TEXT DEFAULT 'result',
            status TEXT DEFAULT 'queued',
            target_query TEXT,
            total_queued INTEGER DEFAULT 0,
            total_sent INTEGER DEFAULT 0,
            total_failed INTEGER DEFAULT 0,
            started_at TEXT,
            completed_at TEXT,
            error_message TEXT,
            last_sender_email TEXT,
            last_sender_sent_at TEXT,
            resume_at TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # EMAIL LOG TABLE — tracks every send attempt
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_email TEXT NOT NULL,
            from_email TEXT,
            subject TEXT,
            body_preview TEXT,
            phase TEXT DEFAULT 'result',
            campaign_id INTEGER,
            message_id TEXT,
            status TEXT DEFAULT 'sent',
            error_message TEXT,
            smtp_response TEXT,
            bounce_detected_at TEXT,
            bounce_type TEXT,
            bounce_reason TEXT,
            delivery_status TEXT DEFAULT 'delivered',
            sent_at TEXT DEFAULT (datetime('now', 'localtime')),
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            hour_bucket TEXT,
            FOREIGN KEY (campaign_id) REFERENCES email_campaign(id)
        )
    """)

    # Migrate: add hour_bucket column to existing email_log tables
    try:
        cursor.execute("ALTER TABLE email_log ADD COLUMN hour_bucket TEXT")
    except Exception:
        pass

    # ============================================================
    # SENDER STATS TABLE
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sender_stats (
            sender_email TEXT NOT NULL,
            hour_bucket TEXT NOT NULL,
            emails_sent INTEGER DEFAULT 0,
            PRIMARY KEY (sender_email, hour_bucket)
        )
    """)

    # ============================================================
    # DAILY SENDER STATS TABLE
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_sender_stats (
            sender_email TEXT NOT NULL,
            day_bucket TEXT NOT NULL,
            emails_sent INTEGER DEFAULT 0,
            PRIMARY KEY (sender_email, day_bucket)
        )
    """)

    # ============================================================
    # INDEXES
    # ============================================================
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_applicants_email ON applicants(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_applicants_domain ON applicants(internship_domain)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_log_email ON processing_log(applicant_email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_campaign_status ON email_campaign(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sender_stats ON sender_stats(sender_email, hour_bucket)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_log_to ON email_log(to_email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_log_status ON email_log(delivery_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_log_campaign ON email_log(campaign_id)")

    # ============================================================
    # CTAs TABLE
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ctas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            url TEXT NOT NULL,
            icon TEXT DEFAULT '',
            bg_color TEXT DEFAULT '#2563eb',
            text_color TEXT DEFAULT '#ffffff',
            email_type TEXT DEFAULT 'result',
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ctas_type ON ctas(email_type, is_active, sort_order)")

    # ============================================================
    # SEED DEFAULT SKILL REQUIREMENTS
    # ============================================================
    from bot_config import DEFAULT_SKILL_REQUIREMENTS
    for domain, data in DEFAULT_SKILL_REQUIREMENTS.items():
        cursor.execute("""
            INSERT OR IGNORE INTO skill_requirements (domain, required_skills, description, certifications, source)
            VALUES (?, ?, ?, ?, 'manual')
        """, (domain, json.dumps(data["required_skills"]), data["description"],
              json.dumps(data.get("certifications", []))))

    # Seed default CTAs
    seed_default_ctas(cursor)

    conn.commit()
    conn.close()


def seed_default_ctas(cursor):
    """Insert default CTAs if none exist."""
    cursor.execute("SELECT COUNT(*) as cnt FROM ctas")
    if cursor.fetchone()["cnt"] > 0:
        return
    defaults = [
        ("Join WhatsApp Group", "https://chat.whatsapp.com/HiKGjnjetq65jHi9UGczYs", "&#128172;", "#25d366", "#ffffff", "result", 1),
        ("Check Your Results", "https://gayatrieducation.tech/results", "&#128202;", "#2563eb", "#ffffff", "result", 2),
    ]
    for label, url, icon, bg, text, etype, sort in defaults:
        cursor.execute("""
            INSERT INTO ctas (label, url, icon, bg_color, text_color, email_type, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
        """, (label, url, icon, bg, text, etype, sort))


# ============================================================
# APPLICANT CRUD
# ============================================================

def upsert_applicant(data: dict):
    """Insert or update an applicant row. Returns the applicant ID."""
    conn = get_connection()
    cursor = conn.cursor()

    fields = {
        "email": data.get("email", ""),
        "name": data.get("name", ""),
        "internship_domain": data.get("internship", ""),
        "college": data.get("college", ""),
        "semester": data.get("semester", ""),
        "whatsapp": data.get("whatsapp", ""),
        "available_2months": data.get("available", ""),
        "has_laptop_internet": data.get("laptop", ""),
        "needs_placement": data.get("placement", ""),
        "cv_file_link": data.get("cv", ""),
        "how_did_you_hear": data.get("hear_about", ""),
        "submitted_at": data.get("timestamp", ""),
        "approval_status": data.get("approval_status", "Pending"),
        "email_sent_at": data.get("email_sent_at"),
        "confirmation_sent_at": data.get("confirmation_sent_at"),
    }

    cursor.execute("SELECT * FROM applicants WHERE email = ?", (fields["email"],))
    row = cursor.fetchone()

    if row:
        # Preserve existing timestamps if not explicitly provided in data
        existing = dict(row)
        if data.get("confirmation_sent_at") is None:
            fields["confirmation_sent_at"] = existing.get("confirmation_sent_at")
        if data.get("email_sent_at") is None:
            fields["email_sent_at"] = existing.get("email_sent_at")

        applicant_id = row["id"]
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), applicant_id]
        cursor.execute(f"""
            UPDATE applicants SET {set_clause}, updated_at = ? WHERE id = ?
        """, values)
    else:
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" * len(fields))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = list(fields.values()) + [now, now]
        cursor.execute(f"""
            INSERT INTO applicants ({columns}, created_at, updated_at)
            VALUES ({placeholders}, ?, ?)
        """, values)

    conn.commit()
    applicant_id = cursor.lastrowid
    conn.close()
    return applicant_id


def get_applicant_by_email(email):
    """Get applicant record by email."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM applicants WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_applicants(limit=None, offset=0, filters=None):
    """Get all applicants with optional filters."""
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM applicants WHERE 1=1"
    params = []

    if filters:
        if filters.get("domain"):
            query += " AND internship_domain = ?"
            params.append(filters["domain"])
        if filters.get("approval_status"):
            query += " AND approval_status = ?"
            params.append(filters["approval_status"])
        if filters.get("upskilling"):
            query += " AND upskilling_course = ?"
            params.append(filters["upskilling"])

    query += " ORDER BY created_at DESC"

    if limit:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_approval_status(email, status):
    """Update approval status for an applicant."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE applicants SET approval_status = ?, updated_at = ? WHERE email = ?
    """, (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email))
    conn.commit()
    conn.close()


def update_email_sent_at(email, sent_time):
    """Update email_sent_at timestamp for an applicant."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE applicants SET email_sent_at = ?, updated_at = ? WHERE email = ?
    """, (sent_time, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email))
    conn.commit()
    conn.close()


def update_confirmation_sent_at(email, sent_time):
    """Update confirmation_sent_at timestamp for an applicant."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE applicants SET confirmation_sent_at = ?, updated_at = ? WHERE email = ?
    """, (sent_time, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email))
    conn.commit()
    conn.close()


def update_offer_sent_at(email, sent_time):
    """Update offer_sent_at timestamp for an applicant."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE applicants SET offer_sent_at = ?, updated_at = ? WHERE email = ?
    """, (sent_time, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email))
    conn.commit()
    conn.close()


def get_offer_letter_targets():
    """
    Return applicants eligible for offer letters.

    Excludes enrolled emails and anyone who already received an offer.
    Ordered by created_at DESC (newest first).
    """
    from enrollment_filter import is_enrolled

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM applicants
        WHERE offer_sent_at IS NULL
          AND confirmation_sent_at IS NOT NULL
        ORDER BY created_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    targets = []
    for row in rows:
        applicant = dict(row)
        email = applicant.get("email", "")
        if is_enrolled(email):
            continue
        targets.append(applicant)

    return targets


def get_reminder_campaign_targets():
    """
    Return applicants eligible for reminder campaigns.

    Targets people who received a confirmation email but haven't yet received
    a campaign reminder. Excludes enrolled emails.
    Ordered by created_at DESC (newest first).
    """
    from enrollment_filter import is_enrolled

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM applicants
        WHERE confirmation_sent_at IS NOT NULL
          AND email_sent_at IS NULL
        ORDER BY created_at DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    targets = []
    for row in rows:
        applicant = dict(row)
        email = applicant.get("email", "")
        if is_enrolled(email):
            continue
        targets.append(applicant)

    return targets


def count_pending_seats(domain=None):
    """Return a static count for reminder emails. Seat tracking removed."""
    return 0


def get_applicant_detail(applicant_id):
    """Get full detail for one applicant."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM applicants WHERE id = ?", (applicant_id,))
    applicant = cursor.fetchone()
    if not applicant:
        conn.close()
        return None

    applicant = dict(applicant)

    # Processing log for this applicant
    cursor.execute("""
        SELECT * FROM processing_log WHERE applicant_email = ? ORDER BY created_at DESC LIMIT 10
    """, (applicant["email"],))
    applicant["processing_log"] = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return applicant


# ============================================================
# SKILL REQUIREMENTS
# ============================================================

def get_skill_requirements(domain):
    """Get skill requirements for a domain."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM skill_requirements WHERE domain = ?", (domain,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_skill_requirements():
    """Get all skill requirements."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM skill_requirements ORDER BY domain")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_skill_requirements(domain, skills_list, description, source="manual", certifications=None):
    """Save or update skill requirements for a domain."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO skill_requirements (domain, required_skills, description, certifications, source, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
    """, (domain, json.dumps(skills_list), description, json.dumps(certifications) if certifications else None, source))
    conn.commit()
    conn.close()


def delete_skill_requirement(domain):
    """Delete a skill requirement entry."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM skill_requirements WHERE domain = ?", (domain,))
    conn.commit()
    conn.close()


# ============================================================
# PROCESSING LOG
# ============================================================

def add_processing_log(applicant_email, action, details=""):
    """Add an entry to the processing log."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO processing_log (applicant_email, action, details)
        VALUES (?, ?, ?)
    """, (applicant_email, action, details))
    conn.commit()
    conn.close()


# ============================================================
# DASHBOARD STATS
# ============================================================

def get_dashboard_stats():
    """Get summary stats for the dashboard."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    cursor.execute("SELECT COUNT(*) as count FROM applicants")
    stats["total_applicants"] = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM applicants WHERE approval_status = 'Pending'")
    stats["pending"] = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM applicants WHERE approval_status = 'Approved'")
    stats["approved"] = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM applicants WHERE approval_status = 'Rejected'")
    stats["rejected"] = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM applicants WHERE confirmation_sent_at IS NOT NULL")
    stats["emails_sent"] = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM applicants WHERE upskilling_course = 'yes'")
    stats["upskilling_yes"] = cursor.fetchone()["count"]

    # By domain
    cursor.execute("""
        SELECT internship_domain, COUNT(*) as count
        FROM applicants
        WHERE internship_domain IS NOT NULL AND internship_domain != ''
        GROUP BY internship_domain
        ORDER BY count DESC
    """)
    stats["by_domain"] = [dict(r) for r in cursor.fetchall()]

    # Recent activity
    cursor.execute("""
        SELECT * FROM processing_log ORDER BY created_at DESC LIMIT 20
    """)
    stats["recent_activity"] = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return stats


# ============================================================
# CTA CRUD
# ============================================================

def get_all_ctas(email_type=None):
    """Get all active CTAs, optionally filtered by email_type."""
    conn = get_connection()
    cursor = conn.cursor()
    if email_type:
        cursor.execute("SELECT * FROM ctas WHERE is_active = 1 AND email_type = ? ORDER BY sort_order ASC, id ASC", (email_type,))
    else:
        cursor.execute("SELECT * FROM ctas ORDER BY email_type, sort_order ASC, id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cta_by_id(cta_id):
    """Get a single CTA by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ctas WHERE id = ?", (cta_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_cta(label, url, icon="", bg_color="#2563eb", text_color="#ffffff", email_type="result", sort_order=0):
    """Create a new CTA."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO ctas (label, url, icon, bg_color, text_color, email_type, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (label, url, icon, bg_color, text_color, email_type, sort_order, now, now))
    conn.commit()
    cta_id = cursor.lastrowid
    conn.close()
    return cta_id


def update_cta(cta_id, **kwargs):
    """Update a CTA."""
    allowed = {"label", "url", "icon", "bg_color", "text_color", "email_type", "sort_order", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [cta_id]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE ctas SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_cta(cta_id):
    """Soft-delete a CTA."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE ctas SET is_active = 0, updated_at = ? WHERE id = ?",
                   (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cta_id))
    conn.commit()
    conn.close()


# ============================================================
# EMAIL CAMPAIGN HELPERS
# ============================================================

def create_campaign(campaign_name, phase="result", target_query=None) -> int:
    """Create a new campaign row."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO email_campaign (campaign_name, phase, status, target_query)
        VALUES (?, ?, 'queued', ?)
    """, (campaign_name, phase, target_query))
    campaign_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return campaign_id


def get_campaign(campaign_id):
    """Get a campaign by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM email_campaign WHERE id = ?", (campaign_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_campaign():
    """Get the currently running or paused campaign."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM email_campaign
        WHERE status IN ('running', 'paused')
        ORDER BY id DESC LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_offer_campaign():
    """Get the currently running or paused offer letter campaign."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM email_campaign
        WHERE status IN ('running', 'paused')
          AND phase = 'offer_letter'
        ORDER BY id DESC LIMIT 1
    """)
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_campaign(campaign_id, **kwargs):
    """Update campaign fields."""
    if not kwargs:
        return
    conn = get_connection()
    cursor = conn.cursor()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [campaign_id]
    cursor.execute(f"UPDATE email_campaign SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def increment_campaign_sent(campaign_id):
    """Atomically increment total_sent by 1."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE email_campaign SET total_sent = total_sent + 1 WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()


def increment_campaign_failed(campaign_id):
    """Atomically increment total_failed by 1."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE email_campaign SET total_failed = total_failed + 1 WHERE id = ?", (campaign_id,))
    conn.commit()
    conn.close()


def get_campaign_stats(campaign_id):
    """Get live stats for a campaign."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT total_queued, total_sent, total_failed, status, started_at
        FROM email_campaign WHERE id = ?
    """, (campaign_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}


def get_campaign_send_count(campaign_id) -> int:
    """Count how many applicants have been sent emails for this campaign."""
    conn = get_connection()
    cursor = conn.cursor()
    campaign = get_campaign(campaign_id)
    if not campaign or not campaign.get("started_at"):
        conn.close()
        return 0
    cursor.execute("""
        SELECT COUNT(*) FROM applicants
        WHERE email_sent_at >= ?
    """, (campaign["started_at"],))
    count = cursor.fetchone()[0]
    conn.close()
    return count


# ============================================================
# SENDER STATS HELPERS
# ============================================================

def get_hour_bucket(dt=None):
    """Return current hour bucket as 'YYYY-MM-DDTHH'."""
    if dt is None:
        from datetime import datetime
        dt = datetime.now()
    return dt.strftime("%Y-%m-%dT%H")


def get_sender_count(sender_email, hour_bucket) -> int:
    """Get how many emails a sender has sent in a given hour."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT emails_sent FROM sender_stats
        WHERE sender_email = ? AND hour_bucket = ?
    """, (sender_email, hour_bucket))
    row = cursor.fetchone()
    conn.close()
    return row["emails_sent"] if row else 0


def increment_sender_count(sender_email, hour_bucket):
    """Increment the send count for a sender in a given hour."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sender_stats (sender_email, hour_bucket, emails_sent)
        VALUES (?, ?, 1)
        ON CONFLICT(sender_email, hour_bucket) DO UPDATE SET emails_sent = emails_sent + 1
    """, (sender_email, hour_bucket))
    conn.commit()
    conn.close()


def get_all_sender_stats() -> list:
    """Get current-hour counts for all senders."""
    from datetime import datetime
    bucket = get_hour_bucket()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sender_email, emails_sent FROM sender_stats
        WHERE hour_bucket = ?
    """, (bucket,))
    rows = cursor.fetchall()
    conn.close()
    result = {r["sender_email"]: r["emails_sent"] for r in rows}

    from bot_config import SENDER_POOL
    for s in SENDER_POOL:
        result.setdefault(s["email"], 0)
    return result


def cleanup_old_sender_stats():
    """Remove sender_stats older than 24 hours."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sender_stats WHERE hour_bucket < ?", (cutoff,))
    conn.commit()
    conn.close()


# ============================================================
# DAILY SENDER STATS
# ============================================================

def get_day_bucket(dt=None):
    """Return current day bucket as 'YYYY-MM-DD'."""
    if dt is None:
        from datetime import datetime
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d")


def get_sender_daily_count(sender_email, day_bucket) -> int:
    """Get how many emails a sender has sent in a given day."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT emails_sent FROM daily_sender_stats
        WHERE sender_email = ? AND day_bucket = ?
    """, (sender_email, day_bucket))
    row = cursor.fetchone()
    conn.close()
    return row["emails_sent"] if row else 0


def increment_sender_daily_count(sender_email, day_bucket):
    """Increment the daily send count for a sender."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO daily_sender_stats (sender_email, day_bucket, emails_sent)
        VALUES (?, ?, 1)
        ON CONFLICT(sender_email, day_bucket) DO UPDATE SET emails_sent = emails_sent + 1
    """, (sender_email, day_bucket))
    conn.commit()
    conn.close()


# ============================================================
# EMAIL LOG HELPERS
# ============================================================

def log_email_send(
    to_email: str,
    from_email: str = "",
    subject: str = "",
    body_preview: str = "",
    phase: str = "result",
    campaign_id: int = None,
    message_id: str = "",
    status: str = "sent",
    error_message: str = "",
    smtp_response: str = "",
) -> int:
    """Record an email send attempt in the email_log table."""
    conn = get_connection()
    cursor = conn.cursor()
    hb = get_hour_bucket()
    cursor.execute("""
        INSERT INTO email_log
            (to_email, from_email, subject, body_preview, phase,
             campaign_id, message_id, status, error_message, smtp_response,
             hour_bucket)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        to_email,
        from_email or None,
        subject or None,
        body_preview[:200] if body_preview else None,
        phase,
        campaign_id,
        message_id or None,
        status,
        error_message or None,
        smtp_response or None,
        hb,
    ))
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def update_email_log(log_id: int, **kwargs) -> None:
    """Update fields on an existing email_log row."""
    if not kwargs:
        return
    conn = get_connection()
    cursor = conn.cursor()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [log_id]
    cursor.execute(f"UPDATE email_log SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def mark_email_bounced(to_email: str, bounce_type: str, bounce_reason: str,
                        message_id: str = "", smtp_response: str = "") -> int:
    """Mark the most recent email_log entry for to_email as bounced."""
    conn = get_connection()
    cursor = conn.cursor()

    row_id = 0
    if message_id:
        cursor.execute("""
            SELECT id FROM email_log
            WHERE to_email = ? AND message_id = ?
            ORDER BY sent_at DESC LIMIT 1
        """, (to_email, message_id))
        row = cursor.fetchone()
        if row:
            row_id = row["id"]

    if not row_id:
        cursor.execute("""
            SELECT id FROM email_log
            WHERE to_email = ?
              AND delivery_status NOT IN ('bounced', 'failed')
            ORDER BY sent_at DESC LIMIT 1
        """, (to_email,))
        row = cursor.fetchone()
        if row:
            row_id = row["id"]

    if row_id:
        cursor.execute("""
            UPDATE email_log
            SET bounce_detected_at = datetime('now', 'localtime'),
                bounce_type = ?, bounce_reason = ?,
                delivery_status = 'bounced',
                smtp_response = COALESCE(NULLIF(?, ''), smtp_response)
            WHERE id = ?
        """, (bounce_type, bounce_reason, smtp_response, row_id))

    conn.commit()
    conn.close()
    return row_id


def get_email_log(limit: int = 50, to_email: str = "", status: str = "",
                   campaign_id: int = None, bounce_only: bool = False) -> list:
    """Return recent email_log rows."""
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM email_log WHERE 1=1"
    params = []

    if to_email:
        query += " AND to_email = ?"
        params.append(to_email)
    if status:
        query += " AND status = ?"
        params.append(status)
    if campaign_id is not None:
        query += " AND campaign_id = ?"
        params.append(campaign_id)
    if bounce_only:
        query += " AND delivery_status = 'bounced'"

    query += " ORDER BY sent_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_email_log_stats() -> dict:
    """Aggregate email_log stats for the dashboard."""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}

    cursor.execute("SELECT COUNT(*) AS c FROM email_log")
    stats["total"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE status = 'sent'")
    stats["sent"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE status = 'failed'")
    stats["failed"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE delivery_status = 'bounced'")
    stats["bounced"] = cursor.fetchone()["c"]

    cursor.execute("""
        SELECT COUNT(*) AS c FROM email_log
        WHERE delivery_status NOT IN ('bounced', 'failed')
    """)
    stats["pending"] = cursor.fetchone()["c"]

    cursor.execute("""
        SELECT phase, COUNT(*) AS c FROM email_log GROUP BY phase
    """)
    stats["by_phase"] = {r["phase"]: r["c"] for r in cursor.fetchall()}

    sent = stats["sent"]
    bounced = stats["bounced"]
    stats["bounce_rate_pct"] = round((bounced / sent) * 100, 2) if sent else 0.0

    conn.close()
    return stats


def get_email_analytics() -> dict:
    """Comprehensive email analytics for the dashboard."""
    conn = get_connection()
    cursor = conn.cursor()

    result = {}

    # ---- Overall totals ----
    cursor.execute("SELECT COUNT(*) AS c FROM email_log")
    result["total"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE status = 'sent'")
    result["sent"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE status = 'failed'")
    result["failed"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE delivery_status = 'bounced'")
    result["bounced"] = cursor.fetchone()["c"]

    # pending = rows not sent and not failed (queued/in-progress)
    cursor.execute("""
        SELECT COUNT(*) AS c FROM email_log
        WHERE status NOT IN ('sent', 'failed')
    """)
    result["pending"] = cursor.fetchone()["c"]

    result["success_rate_pct"] = round((result["sent"] / result["total"]) * 100, 1) if result["total"] else 0.0

    # ---- By phase (result / reminder / offer_letter) ----
    cursor.execute("""
        SELECT phase, status, COUNT(*) AS c FROM email_log
        GROUP BY phase, status
    """)
    phase_rows = cursor.fetchall()
    phase_breakdown = {}
    for r in phase_rows:
        p = r["phase"] or "unknown"
        if p not in phase_breakdown:
            phase_breakdown[p] = {}
        phase_breakdown[p][r["status"]] = r["c"]
    result["by_phase"] = phase_breakdown

    # ---- Per-sender breakdown (overall) ----
    cursor.execute("""
        SELECT from_email, status, COUNT(*) AS c FROM email_log
        WHERE from_email IS NOT NULL
        GROUP BY from_email, status
    """)
    sender_rows = cursor.fetchall()
    sender_breakdown = {}
    for r in sender_rows:
        e = r["from_email"]
        if e not in sender_breakdown:
            sender_breakdown[e] = {"sent": 0, "failed": 0, "total": 0}
        sender_breakdown[e][r["status"]] = r["c"]
        sender_breakdown[e]["total"] += r["c"]
    result["by_sender"] = sender_breakdown

    # ---- Offer letter specific ----
    cursor.execute("""
        SELECT from_email, status, COUNT(*) AS c FROM email_log
        WHERE phase = 'offer_letter'
        GROUP BY from_email, status
    """)
    offer_sender_rows = cursor.fetchall()
    offer_sender_breakdown = {}
    for r in offer_sender_rows:
        e = r["from_email"] or "unknown"
        if e not in offer_sender_breakdown:
            offer_sender_breakdown[e] = {"sent": 0, "failed": 0, "total": 0}
        offer_sender_breakdown[e][r["status"]] = r["c"]
        offer_sender_breakdown[e]["total"] += r["c"]
    result["offer_by_sender"] = offer_sender_breakdown

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE phase = 'offer_letter'")
    result["offer_total"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE phase = 'offer_letter' AND status = 'sent'")
    result["offer_sent"] = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM email_log WHERE phase = 'offer_letter' AND status = 'failed'")
    result["offer_failed"] = cursor.fetchone()["c"]

    # ---- Daily breakdown (last 30 days) ----
    cursor.execute("""
        SELECT DATE(sent_at) as day, COUNT(*) AS c, SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent_cnt
        FROM email_log
        WHERE sent_at >= DATE('now', '-30 days')
        GROUP BY DATE(sent_at)
        ORDER BY day ASC
    """)
    result["daily"] = [
        {"day": r["day"], "total": r["c"], "sent": r["sent_cnt"]}
        for r in cursor.fetchall()
    ]

    # ---- Recent 30 sends ----
    cursor.execute("""
        SELECT to_email, from_email, subject, phase, status, delivery_status, sent_at, error_message
        FROM email_log
        ORDER BY sent_at DESC LIMIT 30
    """)
    result["recent"] = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return result


# ============================================================
# MASTER CSV EXPORT (no CV/enrichment data)
# ============================================================

def get_master_csv_data():
    """Return all applicants as flat rows for CSV export."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            a.id, a.email, a.name, a.internship_domain, a.college,
            a.semester, a.whatsapp, a.available_2months, a.has_laptop_internet,
            a.needs_placement, a.upskilling_course, a.course_name,
            a.cv_file_link, a.how_did_you_hear, a.approval_status,
            a.email_sent_at, a.confirmation_sent_at, a.submitted_at,
            a.created_at, a.updated_at
        FROM applicants a
        ORDER BY a.created_at DESC
    """)

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def import_master_csv(filepath):
    """Import a master CSV file into the applicants table.

    Expected columns: email, name, internship_domain, college, semester,
    whatsapp, available_2months, has_laptop_internet, needs_placement,
    upskilling_course, course_name, cv_file_link, how_did_you_hear

    Returns dict with: status, message, total, inserted, skipped, errors
    """
    import csv
    result = {"status": "ok", "message": "", "total": 0, "inserted": 0, "skipped": 0, "errors": []}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        result["status"] = "error"
        result["message"] = "Failed to read CSV: %s" % e
        return result

    result["total"] = len(rows)

    for i, row in enumerate(rows):
        try:
            email = (row.get("email") or row.get("Email") or "").strip()
            if not email:
                result["skipped"] += 1
                result["errors"].append("Row %d: missing email" % (i + 2))
                continue

            data = {
                "email": email,
                "name": (row.get("name") or row.get("Name") or "").strip(),
                "internship_domain": (row.get("internship_domain") or row.get("Internship Domain") or "").strip(),
                "college": (row.get("college") or row.get("College") or "").strip(),
                "semester": (row.get("semester") or row.get("Semester") or "").strip(),
                "whatsapp": (row.get("whatsapp") or row.get("WhatsApp") or "").strip(),
                "available_2months": (row.get("available_2months") or row.get("Available") or "").strip(),
                "has_laptop_internet": (row.get("has_laptop_internet") or row.get("Laptop") or "").strip(),
                "needs_placement": (row.get("needs_placement") or row.get("Placement") or "").strip(),
                "upskilling_course": (row.get("upskilling_course") or "").strip(),
                "course_name": (row.get("course_name") or "").strip(),
                "cv_file_link": (row.get("cv_file_link") or row.get("CV") or "").strip(),
                "how_did_you_hear": (row.get("how_did_you_hear") or row.get("Hear About") or "").strip(),
                "approval_status": "Pending",
            }
            upsert_applicant(data)
            result["inserted"] += 1
        except Exception as e:
            result["skipped"] += 1
            result["errors"].append("Row %d (%s): %s" % (i + 2, email, e))

    result["message"] = "%d rows processed, %d inserted, %d skipped" % (
        result["total"], result["inserted"], result["skipped"])
    return result