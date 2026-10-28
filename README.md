# GayatriBot — HR Automation System

> Developed under the [Dbert online internship program](https://dbert.online).

Automated HR and recruitment tooling for the Gayatri Education Project. It polls
a Google Sheet fed by a Google Form, sends templated result and reminder emails
through a rotating pool of SMTP senders, generates offer-letter and LOR 
PDFs, and provides a local admin dashboard over a SQLite database.

**Runs on Windows, macOS and Linux.**

---

## Components

| Component | Command | What it does |
|---|---|---|
| Admin dashboard | `python flask_app.py` | Manage applicants, skills, CTAs, campaigns and send controls |
| Email bot | `python bot.py` | Polls the Google Sheet and sends result emails |
| Reminder campaign | *(started from dashboard)* | Bulk follow-up emails, runs as a background thread |
| Offer letters | *(started from dashboard)* | Generates offer PDFs and emails them |
| Archive utility | `python archive_offer_logs.py --yes` | Resets offer state between hiring batches |
| Preflight check | `python doctor.py` | Reports whether this machine is ready to run |

Supporting modules: `config.py` (environment configuration), `database.py`
(SQLite access), `smtp_service.py` (sending, rate limits, retries),
`pdf_renderer.py` (cross-platform DOCX→PDF), `enrollment_filter.py`,
`send_control.py`.

---

## Prerequisites

| | Windows 10/11 | macOS 13+ | Linux (Ubuntu 22.04+) |
|---|---|---|---|
| Python 3.9+ | python.org or `winget` | `brew install python` | `apt install python3` |
| LibreOffice *(recommended)* | [libreoffice.org](https://libreoffice.org) | `brew install --cask libreoffice` | `apt install libreoffice-writer` |

LibreOffice is used to render offer letters from the DOCX template. Without it
the system still runs, falling back to a simpler PDF layout and warning at
startup. See [PDF engine](#pdf-engine).

You will also need, for the flows that use them:

- A **Gmail account with an App Password** for each sender (Google Account →
  Security → App Passwords; requires 2FA).
- A **Google Cloud service account JSON key**, with the service account's email
  granted Editor access to the target spreadsheet.

---

## Installation

```bash
git clone <repository-url>
cd gayatribot-Hr

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

All configuration comes from the environment. Copy the template and fill it in:

```bash
cp .env.example .env              # Windows: copy .env.example .env
```

`.env` is gitignored and **must never be committed**. Real environment variables
take precedence over the file, so CI and production can run without one.

Minimum to start the dashboard:

```ini
GAYATRI_SENDER_1_EMAIL=your-test-account@gmail.com
GAYATRI_SENDER_1_PASSWORD=your-app-password
GAYATRI_TEST_RECIPIENT=your-own-address@example.com
```

To also run the sheet-polling bot:

```ini
GAYATRI_SHEET_ID=<spreadsheet id from the sheet URL>
GAYATRI_SERVICE_ACCOUNT=service_account.json
```

Every variable is documented in [`.env.example`](.env.example).

> **During development, use a developer-owned test Gmail account and a
> non-production spreadsheet.** Production credentials are configured by the
> project owner directly in the production environment.

## Verify the setup

```bash
python doctor.py
```

Reports Python version, dependencies, configuration, PDF engine, database state,
port availability and SMTP DNS resolution. It sends no email and changes nothing.

## Run

```bash
python flask_app.py     # dashboard — http://127.0.0.1:5000/ (5050 on macOS)
python bot.py           # sheet poller, in a second terminal
```

Reminder and offer-letter campaigns are started from the dashboard, not as
standalone processes.

If installed as a package (`pip install -e .`), console commands are available
on every platform: `gayatri-dashboard`, `gayatri-bot`, `gayatri-archive`,
`gayatri-doctor`.

---

## PDF engine

Offer letters are rendered from `sampleoffer.docx`.

| Engine | When used | Output |
|---|---|---|
| **LibreOffice** | Auto-detected if `soffice` is on `PATH` | Full fidelity — matches the DOCX template |
| **ReportLab** | Fallback when LibreOffice is absent | Correct content, simplified layout |

The active engine is shown by `python doctor.py` and on the dashboard settings
page. Force one with `GAYATRI_PDF_ENGINE=libreoffice|reportlab`, or point at a
non-standard install with `GAYATRI_SOFFICE_PATH`.

---

## Security

The dashboard **has no authentication**. It binds to `127.0.0.1` by default and
is reachable only from the machine it runs on.

Changing `GAYATRI_FLASK_HOST` exposes applicant personal data and the ability to
send email campaigns to anyone who can reach the host. Do not change it without
first adding authentication, authorization, HTTPS and network access controls.
Do not expose the development dashboard through port forwarding or a public
reverse proxy.

Never commit `.env`, `service_account.json`, or any file containing a password.

---

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

The suite runs fully offline — no SMTP connection, no Google API call, no
network. Tests that need LibreOffice skip cleanly when it is absent.

CI runs the suite on Windows, macOS and Linux against Python 3.9 and 3.12.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Port 5000 is already in use` on macOS | AirPlay Receiver occupies it. The default is already 5050 on macOS; otherwise set `GAYATRI_FLASK_PORT`, or disable AirPlay Receiver in System Settings → General → AirDrop & Handoff. |
| `Configuration problems found` at startup | A required variable is unset. The message names it; add it to `.env`. |
| Startup warns "reportlab fallback" | LibreOffice was not found. Install it, or set `GAYATRI_SOFFICE_PATH` if it is installed elsewhere. |
| `database is locked` | Should not occur — the database uses WAL with a 30s busy timeout. Check `python doctor.py` reports `journal_mode: wal`. If it reports otherwise, the database may be on a network share; move it to local disk. |
| SMTP authentication failure | Gmail requires an App Password, not the account password, and 2FA must be enabled on the account. |
| `service account key not found` | Set `GAYATRI_SERVICE_ACCOUNT` to the JSON key path, and share the sheet with the service account email inside that file. |
| Sheet sync reports an error | Confirm `GAYATRI_SHEET_ID` is the id from the sheet URL and that the service account has Editor access. |

See [OPERATIONS.md](OPERATIONS.md) for day-to-day operational procedures.
