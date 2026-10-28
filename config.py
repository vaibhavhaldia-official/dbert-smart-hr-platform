"""
config.py — Environment-based configuration for the Gayatri Education Project.

Phase 1 (AC-5, AC-6, AC-7): every secret and deployment-specific value is read
from the process environment, never from source. A local ``.env`` file is loaded
for developer convenience; real environment variables always take precedence, so
CI and production need no file on disk.

Nothing in this module may contain a real credential. ``.env`` is gitignored;
``.env.example`` documents the variable names with empty placeholders.

All filesystem paths resolve to absolute paths anchored at BASE_DIR, so every
entrypoint reads and writes the same files regardless of the working directory
it was launched from (AC-7).

Call ``validate()`` at startup and exit non-zero if it returns problems (AC-6).
"""

import os
import sys
from pathlib import Path

# ============================================================
# BASE DIRECTORY — anchor for every relative path (AC-7)
# ============================================================

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# .env LOADING — real environment wins over file values
# ============================================================

def _load_dotenv():
    """Load BASE_DIR/.env if python-dotenv is available and the file exists.

    ``override=False`` ensures an exported environment variable beats the file,
    which is what lets CI and production run with no .env present at all.
    """
    # Tests set GAYATRI_SKIP_DOTENV so they can never pick up real credentials.
    if os.environ.get("GAYATRI_SKIP_DOTENV"):
        return False

    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        sys.stderr.write(
            "[config] WARNING: %s exists but python-dotenv is not installed; "
            "it will be ignored. Run: pip install python-dotenv\n" % env_path
        )
        return False
    load_dotenv(env_path, override=False)
    return True


DOTENV_LOADED = _load_dotenv()


# ============================================================
# TYPED ENVIRONMENT READERS
# ============================================================

_MISSING = []  # accumulates names of required-but-absent variables


def _env(name, default=None):
    """Read an optional environment variable, falling back to a default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _req(name):
    """Read a required environment variable; record it if absent.

    Returns "" rather than raising so that validate() can report every missing
    variable at once instead of failing on the first one.
    """
    value = os.environ.get(name)
    if value is None or value == "":
        _MISSING.append(name)
        return ""
    return value


def _env_int(name, default):
    """Read an integer environment variable, falling back on a bad value."""
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(
            "[config] WARNING: %s=%r is not an integer; using default %r\n"
            % (name, raw, default)
        )
        return default


def _env_bool(name, default=False):
    """Read a boolean environment variable ('1', 'true', 'yes', 'on')."""
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _abs(path_value):
    """Resolve a path against BASE_DIR unless it is already absolute (AC-7)."""
    p = Path(path_value).expanduser()
    if not p.is_absolute():
        p = BASE_DIR / p
    return p.resolve()


# ============================================================
# SMTP SENDER POOL — credentials from environment only (AC-5)
# ============================================================

SMTP_HOST = _env("GAYATRI_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _env_int("GAYATRI_SMTP_PORT", 587)
SMTP_USE_TLS = _env_bool("GAYATRI_SMTP_USE_TLS", True)
SMTP_DISPLAY_NAME = _env("GAYATRI_SMTP_DISPLAY_NAME", "Gayatri Education HR Team")
DEFAULT_REPLY_TO = _env("GAYATRI_REPLY_TO", "contactus@gayatrieducation.tech")

MAX_SENDERS = 10  # upper bound on GAYATRI_SENDER_<n>_* scanning


def _load_senders():
    """Build SENDER_POOL from GAYATRI_SENDER_<n>_EMAIL / _PASSWORD / _NAME.

    Scanning stops being meaningful past MAX_SENDERS; gaps in the numbering are
    skipped rather than treated as the end, so an operator who removes sender 2
    does not silently lose senders 3 and 4.
    """
    senders = []
    for i in range(1, MAX_SENDERS + 1):
        email = _env("GAYATRI_SENDER_%d_EMAIL" % i)
        if not email:
            continue
        password = _env("GAYATRI_SENDER_%d_PASSWORD" % i, "")
        senders.append({
            "email": email,
            "password": password,
            "name": _env("GAYATRI_SENDER_%d_NAME" % i, SMTP_DISPLAY_NAME),
            "server": _env("GAYATRI_SENDER_%d_HOST" % i, SMTP_HOST),
            "port": _env_int("GAYATRI_SENDER_%d_PORT" % i, SMTP_PORT),
            "use_tls": SMTP_USE_TLS,
        })
    return senders


SENDER_POOL = _load_senders()


# ============================================================
# GOOGLE SHEETS (AC-5: ids and key path stay out of source)
# ============================================================

GOOGLE_SHEET_NAME = _env("GAYATRI_SHEET_NAME", "Gayatri Education Project Responses")
GOOGLE_SHEET_ID = _env("GAYATRI_SHEET_ID", "")
GOOGLE_SHEET_GID = _env("GAYATRI_SHEET_GID", "0")
SERVICE_ACCOUNT_KEY = str(_abs(_env("GAYATRI_SERVICE_ACCOUNT", "service_account.json")))


# ============================================================
# DATABASE — absolute path, working-directory independent (AC-7)
# ============================================================

DATABASE_FILE = str(_abs(_env("GAYATRI_DB", "gayatri.db")))
DB_TIMEOUT_SECONDS = float(_env("GAYATRI_DB_TIMEOUT", "30") or 30)
DB_BUSY_TIMEOUT_MS = _env_int("GAYATRI_DB_BUSY_TIMEOUT_MS", 30000)


# ============================================================
# FLASK DASHBOARD
# ============================================================

def _default_port():
    """5000 everywhere except macOS, where AirPlay Receiver occupies it.

    On a stock macOS the ControlCenter process binds port 5000, so the shipped
    default would fail to start. Overridable via GAYATRI_FLASK_PORT.
    """
    return 5050 if sys.platform == "darwin" else 5000


# Default to loopback: the dashboard has no authentication, so it must not be
# reachable from the network without deliberate operator action (R-7, remedy 4).
FLASK_HOST = _env("GAYATRI_FLASK_HOST", "127.0.0.1")
FLASK_PORT = _env_int("GAYATRI_FLASK_PORT", _default_port())
FLASK_DEBUG = _env_bool("GAYATRI_FLASK_DEBUG", False)

# Session signing key. A random per-process default keeps development working
# without a committed secret; sessions simply do not survive a restart.
FLASK_SECRET_KEY = _env("GAYATRI_SECRET_KEY")
if not FLASK_SECRET_KEY:
    import secrets as _secrets
    FLASK_SECRET_KEY = _secrets.token_hex(32)
    FLASK_SECRET_KEY_IS_EPHEMERAL = True
else:
    FLASK_SECRET_KEY_IS_EPHEMERAL = False


# ============================================================
# PDF RENDERING (see pdf_renderer.py)
# ============================================================

PDF_ENGINE = _env("GAYATRI_PDF_ENGINE")  # None = auto-detect
PDF_TIMEOUT_SECONDS = _env_int("GAYATRI_PDF_TIMEOUT", 120)
SOFFICE_PATH = _env("GAYATRI_SOFFICE_PATH")  # explicit override


# ============================================================
# POLLING / RATE LIMITS / RETRY
# ============================================================

POLL_INTERVAL_SECONDS = _env_int("GAYATRI_POLL_INTERVAL", 300)

SMTP_MIN_DELAY_SECONDS = _env_int("GAYATRI_SMTP_MIN_DELAY", 50)
SMTP_MAX_DELAY_SECONDS = _env_int("GAYATRI_SMTP_MAX_DELAY", 70)
RATE_LIMIT_PER_SENDER_PER_HOUR = _env_int("GAYATRI_RATE_LIMIT_HOUR", 25)
RATE_LIMIT_PER_SENDER_PER_DAY = _env_int("GAYATRI_RATE_LIMIT_DAY", 360)

MAX_RETRIES = _env_int("GAYATRI_MAX_RETRIES", 3)
RETRY_BACKOFF_BASE = _env_int("GAYATRI_RETRY_BACKOFF", 30)


# ============================================================
# PATHS FOR RUNTIME ARTIFACTS (AC-7)
# ============================================================

LOG_FILE = str(_abs(_env("GAYATRI_LOG_FILE", "bot.log")))
OUTPUT_DIR = str(_abs(_env("GAYATRI_OUTPUT_DIR", "generated_offers")))
UPLOAD_FOLDER = str(_abs(_env("GAYATRI_UPLOAD_DIR", "uploads")))
ENROLLMENT_CSV = str(_abs(_env("GAYATRI_ENROLLMENT_CSV", "enrollment.csv")))
SELECTED_CSV = str(_abs(_env("GAYATRI_SELECTED_CSV", "selected2.csv")))
SAMPLE_OFFER_DOCX = str(_abs(_env("GAYATRI_OFFER_TEMPLATE", "sampleoffer.docx")))

# Test-email recipient (W-3): was hardcoded to a personal address in flask_app.
TEST_EMAIL_RECIPIENT = _env("GAYATRI_TEST_RECIPIENT", "")

DAILY_SUMMARY_RECIPIENT = _env("GAYATRI_SUMMARY_RECIPIENT", "")


# ============================================================
# VALIDATION (AC-6)
# ============================================================

def validate(require_sheets=False, require_smtp=False):
    """Return a list of human-readable configuration problems.

    Empty list means the configuration is usable. Callers print each problem and
    exit non-zero. Requirements differ per entrypoint: the dashboard can start
    without Google Sheets access, but bot.py cannot do its job without it.
    """
    problems = []

    for name in _MISSING:
        problems.append(
            "%s is required but not set. Add it to your .env file or export it."
            % name
        )

    if require_sheets:
        if not GOOGLE_SHEET_ID:
            problems.append(
                "GAYATRI_SHEET_ID is not set. It is the spreadsheet id from the "
                "Google Sheet URL (docs.google.com/spreadsheets/d/<ID>/edit)."
            )
        if not Path(SERVICE_ACCOUNT_KEY).exists():
            problems.append(
                "Google service account key not found at %s. Set "
                "GAYATRI_SERVICE_ACCOUNT to its path." % SERVICE_ACCOUNT_KEY
            )

    if require_smtp:
        if not SENDER_POOL:
            problems.append(
                "No SMTP senders configured. Set at least "
                "GAYATRI_SENDER_1_EMAIL and GAYATRI_SENDER_1_PASSWORD."
            )
        for idx, sender in enumerate(SENDER_POOL, start=1):
            if not sender["password"]:
                problems.append(
                    "GAYATRI_SENDER_%d_PASSWORD is empty for sender %s."
                    % (idx, sender["email"])
                )

    if SMTP_MIN_DELAY_SECONDS > SMTP_MAX_DELAY_SECONDS:
        problems.append(
            "GAYATRI_SMTP_MIN_DELAY (%d) exceeds GAYATRI_SMTP_MAX_DELAY (%d)."
            % (SMTP_MIN_DELAY_SECONDS, SMTP_MAX_DELAY_SECONDS)
        )

    return problems


def require_valid(require_sheets=False, require_smtp=False):
    """Print configuration problems and exit non-zero, or return cleanly (AC-6).

    Deliberately prints a plain message with no traceback: a missing environment
    variable is an operator error, not a crash to debug.
    """
    problems = validate(require_sheets=require_sheets, require_smtp=require_smtp)
    if problems:
        sys.stderr.write("\nConfiguration problems found:\n\n")
        for problem in problems:
            sys.stderr.write("  - %s\n" % problem)
        sys.stderr.write(
            "\nSee .env.example for the full list of variables, and run "
            "`make doctor` for a full preflight report.\n\n"
        )
        sys.exit(1)


def summary():
    """Return a dict describing effective configuration, with no secrets.

    Used by `make doctor` and the dashboard settings page.
    """
    return {
        "base_dir": str(BASE_DIR),
        "dotenv_loaded": DOTENV_LOADED,
        "database_file": DATABASE_FILE,
        "flask_host": FLASK_HOST,
        "flask_port": FLASK_PORT,
        "sender_count": len(SENDER_POOL),
        "sender_emails": [s["email"] for s in SENDER_POOL],
        "sheet_id_set": bool(GOOGLE_SHEET_ID),
        "service_account_present": Path(SERVICE_ACCOUNT_KEY).exists(),
        "service_account_path": SERVICE_ACCOUNT_KEY,
        "output_dir": OUTPUT_DIR,
        "offer_template": SAMPLE_OFFER_DOCX,
        "secret_key_ephemeral": FLASK_SECRET_KEY_IS_EPHEMERAL,
    }
