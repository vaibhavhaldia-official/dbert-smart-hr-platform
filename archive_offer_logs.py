"""
archive_offer_logs.py
=====================
Archives all offer letter sending state and resets for a fresh batch.

What this script does:
  1. Backs up gayatri.db â†’ gayatri_backup_<timestamp>.db
  2. Creates email_campaign_archive table and copies all offer campaign rows into it
  3. Creates email_log_archive table and copies all offer_letter phase rows into it
  4. Deletes the archived rows from the live tables
  5. Resets applicants.offer_sent_at to NULL for all rows
  6. Clears sender_stats table (rate-limit counters)
  7. Moves offer_letter.log â†’ logs/archived/offer_letter_<timestamp>.log
  8. Moves all PDFs from generated_offers/ â†’ generated_offers/archived/batch_<timestamp>/

Run with:  python archive_offer_logs.py
"""

import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(BASE_DIR, "gayatri.db")
LOG_PATH  = os.path.join(BASE_DIR, "offer_letter.log")
LOG_ARCHIVE_DIR   = os.path.join(BASE_DIR, "logs", "archived")
PDF_DIR           = os.path.join(BASE_DIR, "generated_offers")
PDF_ARCHIVE_BASE  = os.path.join(BASE_DIR, "generated_offers", "archived")

TIMESTAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def step(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def done(msg):
    print(f"  [OK] {msg}")


def warn(msg):
    print(f"  [!]  {msg}")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# STEP 1 â€” Backup the database
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
step("STEP 1: Backing up gayatri.db")

backup_path = os.path.join(BASE_DIR, f"gayatri_backup_{TIMESTAMP}.db")
shutil.copy2(DB_PATH, backup_path)
done(f"Database backed up â†’ {os.path.basename(backup_path)}")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# STEP 2 â€” Archive DB rows and reset state
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
step("STEP 2: Archiving DB rows and resetting offer state")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 2a. Create archive tables if they don't exist
cur.execute("""
    CREATE TABLE IF NOT EXISTS email_campaign_archive (
        id INTEGER,
        campaign_name TEXT,
        phase TEXT,
        status TEXT,
        target_query TEXT,
        total_queued INTEGER,
        total_sent INTEGER,
        total_failed INTEGER,
        started_at TEXT,
        completed_at TEXT,
        error_message TEXT,
        last_sender_email TEXT,
        last_sender_sent_at TEXT,
        resume_at TEXT,
        created_at TEXT,
        archived_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
""")

cur.execute("""
    CREATE TABLE IF NOT EXISTS email_log_archive (
        id INTEGER,
        to_email TEXT,
        from_email TEXT,
        subject TEXT,
        body_preview TEXT,
        phase TEXT,
        campaign_id INTEGER,
        message_id TEXT,
        status TEXT,
        error_message TEXT,
        smtp_response TEXT,
        bounce_detected_at TEXT,
        bounce_type TEXT,
        bounce_reason TEXT,
        delivery_status TEXT,
        sent_at TEXT,
        created_at TEXT,
        hour_bucket TEXT,
        archived_at TEXT DEFAULT (datetime('now', 'localtime'))
    )
""")
conn.commit()
done("Archive tables created (email_campaign_archive, email_log_archive)")

# 2b. Copy offer campaign rows into archive
cur.execute("""
    INSERT INTO email_campaign_archive
        (id, campaign_name, phase, status, target_query, total_queued, total_sent,
         total_failed, started_at, completed_at, error_message, last_sender_email,
         last_sender_sent_at, resume_at, created_at)
    SELECT id, campaign_name, phase, status, target_query, total_queued, total_sent,
           total_failed, started_at, completed_at, error_message, last_sender_email,
           last_sender_sent_at, resume_at, created_at
    FROM email_campaign
    WHERE phase = 'offer_letter'
""")
campaign_count = cur.rowcount
conn.commit()
done(f"Archived {campaign_count} rows from email_campaign")

# 2c. Copy offer email_log rows into archive
cur.execute("""
    INSERT INTO email_log_archive
        (id, to_email, from_email, subject, body_preview, phase, campaign_id,
         message_id, status, error_message, smtp_response, bounce_detected_at,
         bounce_type, bounce_reason, delivery_status, sent_at, created_at, hour_bucket)
    SELECT id, to_email, from_email, subject, body_preview, phase, campaign_id,
           message_id, status, error_message, smtp_response, bounce_detected_at,
           bounce_type, bounce_reason, delivery_status, sent_at, created_at, hour_bucket
    FROM email_log
    WHERE phase = 'offer_letter'
""")
log_count = cur.rowcount
conn.commit()
done(f"Archived {log_count} rows from email_log")

# 2d. Delete from live tables
cur.execute("DELETE FROM email_campaign WHERE phase = 'offer_letter'")
conn.commit()
done(f"Deleted {campaign_count} rows from email_campaign")

cur.execute("DELETE FROM email_log WHERE phase = 'offer_letter'")
conn.commit()
done(f"Deleted {log_count} rows from email_log")

# 2e. Reset offer_sent_at on all applicants
cur.execute("UPDATE applicants SET offer_sent_at = NULL WHERE offer_sent_at IS NOT NULL")
reset_count = cur.rowcount
conn.commit()
done(f"Reset offer_sent_at to NULL for {reset_count} applicants")

# 2f. Clear sender_stats (rate-limit counters)
cur.execute("DELETE FROM sender_stats")
stats_count = cur.rowcount
conn.commit()
done(f"Cleared {stats_count} rows from sender_stats")

# 2g. Clear daily_sender_stats too
cur.execute("DELETE FROM daily_sender_stats")
daily_count = cur.rowcount
conn.commit()
done(f"Cleared {daily_count} rows from daily_sender_stats")

conn.close()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# STEP 3 â€” Archive offer_letter.log
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
step("STEP 3: Archiving offer_letter.log")

os.makedirs(LOG_ARCHIVE_DIR, exist_ok=True)

if os.path.exists(LOG_PATH):
    archived_log = os.path.join(LOG_ARCHIVE_DIR, f"offer_letter_{TIMESTAMP}.log")
    # Copy contents to archive first (works even if file is locked by running process)
    shutil.copy2(LOG_PATH, archived_log)
    done(f"Copied offer_letter.log -> logs/archived/offer_letter_{TIMESTAMP}.log")
    # Truncate the original to zero bytes in-place (works while file is open by another process)
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.truncate(0)
        done("Truncated offer_letter.log to zero bytes (fresh start)")
    except PermissionError:
        warn("Could not truncate offer_letter.log (still locked) — archive copy saved, log will auto-rotate on next restart")
else:
    warn("offer_letter.log not found -- nothing to archive")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# STEP 4 â€” Archive generated PDFs
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
step("STEP 4: Archiving generated offer PDFs")

pdf_archive_dest = os.path.join(PDF_ARCHIVE_BASE, f"batch_{TIMESTAMP}")
os.makedirs(pdf_archive_dest, exist_ok=True)

pdf_count = 0
for fname in os.listdir(PDF_DIR):
    fpath = os.path.join(PDF_DIR, fname)
    if os.path.isfile(fpath) and fname.lower().endswith(".pdf"):
        shutil.move(fpath, os.path.join(pdf_archive_dest, fname))
        pdf_count += 1

if pdf_count:
    done(f"Moved {pdf_count} PDFs â†’ generated_offers/archived/batch_{TIMESTAMP}/")
else:
    warn("No PDFs found in generated_offers/ â€” nothing to move")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# DONE â€” Summary
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print(f"""
{'='*60}
  ARCHIVE COMPLETE â€” SUMMARY
{'='*60}
  DB backup          : gayatri_backup_{TIMESTAMP}.db
  Campaigns archived : {campaign_count}
  Email logs archived: {log_count}
  offer_sent_at reset: {reset_count} applicants
  sender_stats reset : {stats_count} rows cleared
  Log file archived  : offer_letter.log â†’ logs/archived/
  PDFs archived      : {pdf_count} files â†’ generated_offers/archived/batch_{TIMESTAMP}/
{'='*60}
  The system is now ready for a FRESH offer letter campaign.
{'='*60}
""")

