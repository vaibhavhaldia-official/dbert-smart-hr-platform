"""
enrollment_filter.py — Enrollment guard for the Gayatri Education Project.

Reads enrolled.csv from the project root. Any email found in the CSV
is considered already enrolled and must be skipped by ALL processing
flows (CV processing, result emails, reminder campaigns).

Usage:
    from enrollment_filter import is_enrolled, load_enrollments
    if is_enrolled(applicant_email):
        return  # skip — already enrolled

CSV format:
    email       (required, case-insensitive)
    name        (optional — for logging/reference)
    batch       (optional — e.g. "17 Aug Batch")
"""

import csv
import os
import logging
from pathlib import Path

logger = logging.getLogger("enrollment_filter")

ENROLLED_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enrollment.csv")

# Also check for legacy filename
LEGACY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enrolled.csv")
if not os.path.exists(ENROLLED_CSV_PATH) and os.path.exists(LEGACY_PATH):
    ENROLLED_CSV_PATH = LEGACY_PATH

_enrolled_emails = None  # cache — populated on first load


def load_enrollments(csv_path: str = None) -> set:
    """
    Load enrolled emails from CSV into a set (lowercase for case-insensitive matching).

    Expected CSV columns:
        email    (required)
        name     (optional)
        batch    (optional)

    Returns a set of lowercase email addresses.
    """
    global _enrolled_emails
    path = csv_path or ENROLLED_CSV_PATH

    if not os.path.exists(path):
        logger.warning(f"enrolled.csv not found at {path} — enrollment filtering disabled. "
                       "All emails will be processed.")
        _enrolled_emails = set()
        return _enrolled_emails

    emails = set()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Try common header names
            for row in reader:
                raw = row.get("email") or row.get("Email") or row.get("EMAIL") or row.get("Email Address")
                if raw:
                    emails.add(raw.strip().lower())
        _enrolled_emails = emails
        logger.info(f"Loaded {len(emails)} enrolled emails from {path}")
    except Exception as e:
        logger.error(f"Failed to load enrolled.csv: {e}")
        _enrolled_emails = set()

    return _enrolled_emails


def is_enrolled(email: str) -> bool:
    """
    Check if an email is in the enrolled list.

    Returns True if the email IS enrolled (should be SKIPPED).
    Returns False if the email is NOT enrolled (should be PROCESSED).
    """
    if _enrolled_emails is None:
        load_enrollments()
    if not _enrolled_emails:
        return False  # no CSV loaded — process everything
    return email.strip().lower() in _enrolled_emails


def get_enrolled_count() -> int:
    """Return the number of enrolled emails currently loaded."""
    if _enrolled_emails is None:
        load_enrollments()
    return len(_enrolled_emails or set())


def reload():
    """Force reload the CSV (call after the file is updated)."""
    global _enrolled_emails
    _enrolled_emails = None
    return load_enrollments()
