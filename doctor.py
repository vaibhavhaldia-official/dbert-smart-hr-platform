"""
doctor.py — Preflight diagnostics (AC-10).

Reports whether this machine is ready to run GayatriBot: Python version, PDF
engine, configuration validity, database reachability and journal mode, port
availability, and SMTP host resolution.

Performs no destructive action and sends no email. Exits 0 when ready,
non-zero when something needs attention.
"""

import os
import socket
import sqlite3
import sys
from pathlib import Path

OK = "  [ok]  "
WARN = "  [warn]"
FAIL = "  [FAIL]"


def _section(title):
    print("\n%s" % title)
    print("-" * len(title))


def check_python():
    _section("Python")
    v = sys.version_info
    print("%s %d.%d.%d on %s" % (OK, v.major, v.minor, v.micro, sys.platform))
    if v < (3, 9):
        print("%s Python 3.9 or newer is required." % FAIL)
        return False
    return True


def check_dependencies():
    _section("Dependencies")
    ok = True
    required = [
        ("flask", "Flask dashboard"),
        ("docx", "python-docx — DOCX templating"),
        ("pypdf", "PDF metadata"),
    ]
    optional = [
        ("dotenv", "python-dotenv — .env loading"),
        ("reportlab", "reportlab — fallback PDF engine"),
        ("gspread", "gspread — Google Sheets polling"),
        ("googleapiclient", "google-api-python-client — sheet writes"),
    ]
    for module, label in required:
        try:
            __import__(module)
            print("%s %s" % (OK, label))
        except ImportError:
            print("%s %s is missing (pip install -r requirements.txt)" % (FAIL, label))
            ok = False
    for module, label in optional:
        try:
            __import__(module)
            print("%s %s" % (OK, label))
        except ImportError:
            print("%s %s is not installed" % (WARN, label))
    return ok


def check_config():
    _section("Configuration")
    try:
        import config
    except Exception as exc:
        print("%s config.py failed to load: %s" % (FAIL, exc))
        return False

    s = config.summary()
    print("%s project directory: %s" % (OK, s["base_dir"]))
    print("%s .env loaded: %s" % (OK, "yes" if s["dotenv_loaded"] else "no (using environment only)"))
    print("%s SMTP senders configured: %d" % (OK if s["sender_count"] else WARN, s["sender_count"]))
    for email in s["sender_emails"]:
        print("         - %s" % email)
    print("%s Google Sheet id set: %s" % (OK if s["sheet_id_set"] else WARN, s["sheet_id_set"]))
    print("%s service account key present: %s" % (
        OK if s["service_account_present"] else WARN, s["service_account_present"]))
    if not s["service_account_present"]:
        print("         expected at %s" % s["service_account_path"])
    if s["secret_key_ephemeral"]:
        print("%s GAYATRI_SECRET_KEY unset — sessions reset on restart" % WARN)

    problems = config.validate()
    for problem in problems:
        print("%s %s" % (WARN, problem))
    return True


def check_pdf():
    _section("PDF engine")
    try:
        import pdf_renderer
    except Exception as exc:
        print("%s pdf_renderer failed to load: %s" % (FAIL, exc))
        return False

    detail = pdf_renderer.strategy_detail()
    if detail["strategy"] == "libreoffice":
        print("%s libreoffice — full fidelity" % OK)
        print("         %s" % detail["soffice_path"])
    else:
        print("%s reportlab fallback — simplified layout" % WARN)
        print("         Install LibreOffice for full-fidelity offer letters,")
        print("         or set GAYATRI_SOFFICE_PATH if it is already installed.")
    if detail["forced"]:
        print("         (forced via GAYATRI_PDF_ENGINE)")

    try:
        from bot_config import SAMPLE_OFFER_DOCX
        if Path(SAMPLE_OFFER_DOCX).exists():
            print("%s offer template: %s" % (OK, SAMPLE_OFFER_DOCX))
        else:
            print("%s offer template not found at %s" % (WARN, SAMPLE_OFFER_DOCX))
    except Exception:
        pass
    return True


def check_database():
    _section("Database")
    try:
        from bot_config import DATABASE_FILE
    except Exception as exc:
        print("%s could not resolve database path: %s" % (FAIL, exc))
        return False

    path = Path(DATABASE_FILE)
    print("%s path: %s" % (OK, path))
    if not path.parent.exists():
        print("%s parent directory does not exist" % FAIL)
        return False

    if not path.exists():
        print("%s database does not exist yet — it is created on first run" % WARN)
        return True

    try:
        conn = sqlite3.connect(str(path), timeout=5)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        applicants = 0
        try:
            applicants = conn.execute("SELECT COUNT(*) FROM applicants").fetchone()[0]
        except sqlite3.Error:
            pass
        conn.close()
    except sqlite3.Error as exc:
        print("%s cannot open database: %s" % (FAIL, exc))
        return False

    print("%s tables: %d, applicants: %d" % (OK, tables, applicants))
    if str(mode).lower() == "wal":
        print("%s journal_mode: wal" % OK)
    else:
        print("%s journal_mode: %s (expected wal; set on next app start)" % (WARN, mode))
    return True


def check_port():
    _section("Dashboard port")
    try:
        from bot_config import FLASK_HOST, FLASK_PORT
    except Exception as exc:
        print("%s could not resolve host/port: %s" % (FAIL, exc))
        return False

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((FLASK_HOST if FLASK_HOST != "0.0.0.0" else "", FLASK_PORT))
        free = True
    except OSError:
        free = False
    finally:
        probe.close()

    if free:
        print("%s http://%s:%d/ is available" % (OK, FLASK_HOST, FLASK_PORT))
    else:
        print("%s port %d is already in use on %s" % (WARN, FLASK_PORT, FLASK_HOST))
        if sys.platform == "darwin" and FLASK_PORT == 5000:
            print("         On macOS this is usually the AirPlay Receiver.")
        print("         Set GAYATRI_FLASK_PORT to choose another port.")

    if FLASK_HOST not in ("127.0.0.1", "localhost", "::1"):
        print("%s binding to %s, not loopback." % (WARN, FLASK_HOST))
        print("         The dashboard has NO AUTHENTICATION. Anyone who can reach")
        print("         this host can read applicant data and send campaigns.")
    else:
        print("%s bound to loopback — not reachable from the network" % OK)
    return True


def check_smtp_dns():
    _section("SMTP reachability")
    try:
        from bot_config import SENDER_POOL
    except Exception as exc:
        print("%s could not read sender pool: %s" % (FAIL, exc))
        return False

    if not SENDER_POOL:
        print("%s no senders configured" % WARN)
        return True

    hosts = sorted({s.get("server", "") for s in SENDER_POOL if s.get("server")})
    for host in hosts:
        try:
            socket.getaddrinfo(host, None)
            print("%s %s resolves" % (OK, host))
        except socket.gaierror as exc:
            print("%s %s does not resolve (%s)" % (WARN, host, exc))
    print("         (DNS only — no connection is opened and no mail is sent)")
    return True


def main():
    print("=" * 60)
    print("  GayatriBot — preflight check")
    print("=" * 60)

    results = [
        check_python(),
        check_dependencies(),
        check_config(),
        check_pdf(),
        check_database(),
        check_port(),
        check_smtp_dns(),
    ]

    print("\n" + "=" * 60)
    if all(results):
        print("  Ready.")
        print("=" * 60 + "\n")
        return 0
    print("  Not ready — see [FAIL] items above.")
    print("=" * 60 + "\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
