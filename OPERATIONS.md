# GayatriBot — Operations Runbook

Day-to-day procedures for running the system. For installation see
[README.md](README.md).

---

## Starting and stopping

| Component | Start | Stop |
|---|---|---|
| Dashboard | `python flask_app.py` | `Ctrl+C` in its terminal |
| Email bot | `python bot.py` | `Ctrl+C` in its terminal |
| Reminder campaign | Dashboard → Start campaign | Dashboard → Pause |
| Offer letters | Dashboard → Start offer campaign | Dashboard → Pause |

Each long-running component owns a terminal. `Ctrl+C` stops it cleanly on every
platform — there is no hidden background process and no launcher script.

Campaigns run as background threads inside the dashboard process. **Stopping the
dashboard stops any running campaign**; progress is recorded in the database, so
a campaign can be resumed afterwards from the dashboard.

## Pausing sends

Pause flags take effect at the next loop iteration and survive restarts (they
are sentinel files in the project directory).

| Scope | Dashboard control | Effect |
|---|---|---|
| Everything | Global pause | Blocks bot, campaigns and offers |
| Bot only | Bot pause | Result emails stop; campaigns continue |
| Campaigns only | Campaigns pause | Reminders stop |
| Offers only | Offers pause | Offer letters stop |

**Emergency stop** (dashboard → Terminate all) sets every pause flag and marks
running campaigns stopped. Processes wind down at their next iteration; to stop
one immediately, press `Ctrl+C` in its terminal.

To resume, clear the corresponding pause from the dashboard.

## Monitoring

- **Dashboard home** — applicant counts, campaign progress, per-sender rate-limit
  usage, pause states.
- **Email log** (`/email-log`) — every send attempt with status and bounce info.
- **Log files** — `bot.log`, `flask_dashboard.log`, `offer_letter.log`.
- **Preflight** — `python doctor.py` at any time; it is read-only.

Rate limits are shared across all flows: each sender is capped per hour and per
day across result emails, reminders and offer letters combined.

## Rotating credentials

1. Revoke the old App Password in the Google Account (Security → App Passwords).
2. Generate a replacement.
3. Update `GAYATRI_SENDER_<n>_PASSWORD` in the environment (or `.env`).
4. Restart the dashboard and bot so they pick up the new value.
5. Verify with `python doctor.py`, then send a test email from the dashboard.

Rotate immediately if a credential is ever committed, shared, or logged. A
credential that has been exposed stays compromised even after it is removed from
the repository, because the history and any clones still contain it.

## Backing up the database

`gayatri.db` holds all applicant and email history. Back it up before any bulk
operation.

```bash
python -c "import sqlite3,shutil; \
  src=sqlite3.connect('gayatri.db'); dst=sqlite3.connect('gayatri-backup.db'); \
  src.backup(dst); dst.close(); src.close()"
```

Using SQLite's backup API rather than copying the file captures a consistent
snapshot even while the system is running. The database uses WAL mode, so a
plain file copy can miss recent writes held in `gayatri.db-wal`.

Keep backups off the working machine. They contain applicant personal data.

## Archiving an offer-letter batch

Between hiring batches, reset offer state:

```bash
python archive_offer_logs.py          # dry run — reports what it would do
python archive_offer_logs.py --yes    # performs the reset
```

This backs up the database, moves offer campaign and email-log rows into archive
tables, resets `offer_sent_at` for every applicant, clears rate-limit counters,
and archives the offer log and generated PDFs.

**It is destructive.** Take a backup first and confirm no campaign is running.

## Deployment requirements

The dashboard has **no authentication, no authorization, no audit logging and no
HTTPS**. It binds to `127.0.0.1` and must stay that way until those exist.

Before exposing it beyond the local machine, all of the following are required:

- Authentication for every route.
- Authorization, so that only HR staff can approve applicants or send campaigns.
- HTTPS, terminated by a reverse proxy.
- Audit logging of approvals, rejections and campaign launches.
- Network access controls limiting who can reach the host.
- A non-ephemeral `GAYATRI_SECRET_KEY` so sessions survive restarts.

Until then: do not change `GAYATRI_FLASK_HOST`, and do not expose the dashboard
through port forwarding, a tunnel, or a public reverse proxy.

## Handover checklist

For the project owner taking over a deployment:

- [ ] Configure production credentials directly in the production environment,
      not in any file committed to the repository.
- [ ] Set `GAYATRI_SENDER_<n>_EMAIL` / `_PASSWORD` for each sender mailbox.
- [ ] Set `GAYATRI_SHEET_ID` and place the service account JSON key on the host,
      referenced by `GAYATRI_SERVICE_ACCOUNT`.
- [ ] Grant the service account email Editor access to the production sheet.
- [ ] Set `GAYATRI_SECRET_KEY` to a long random value.
- [ ] Set `GAYATRI_TEST_RECIPIENT` to an address you control.
- [ ] Install LibreOffice for full-fidelity offer letters.
- [ ] Run `python doctor.py` and confirm it reports ready.
- [ ] Send a test email of each template from the dashboard and check rendering.
- [ ] Confirm `GAYATRI_FLASK_HOST` is `127.0.0.1` unless the deployment
      requirements above have been met.
- [ ] Establish a database backup schedule.
