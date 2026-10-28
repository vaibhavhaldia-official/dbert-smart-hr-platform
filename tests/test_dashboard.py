"""Dashboard routes, security defaults and Google Sheets fixes (AC-9, AC-14, F-7, F-8)."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def client(temp_db, monkeypatch, tmp_path):
    monkeypatch.setenv("GAYATRI_UPLOAD_DIR", str(tmp_path / "uploads"))
    import flask_app
    importlib.reload(flask_app)
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


class TestRoutesRender:
    @pytest.mark.parametrize("path", [
        "/", "/applicants", "/skills", "/ctas", "/email-log", "/settings",
    ])
    def test_page_returns_200(self, client, path):
        """AC-14 smoke: every dashboard page renders."""
        assert client.get(path).status_code == 200

    def test_api_stats_is_json(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        assert "total_applicants" in resp.get_json()

    def test_send_control_status(self, client):
        assert client.get("/api/send-control/status").status_code == 200

    def test_email_analytics(self, client):
        assert client.get("/api/email-analytics").status_code == 200

    def test_master_csv_export(self, client):
        resp = client.get("/download/master")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]


class TestGoogleSheetsFixes:
    def test_no_bare_credentials_reference(self):
        """F-7: call sites must use the aliases that are actually imported."""
        source = Path(__file__).resolve().parent.parent / "flask_app.py"
        text = source.read_text(encoding="utf-8")
        assert "= Credentials.from_service_account_file" not in text
        assert '= build("sheets"' not in text
        assert "_GoogleCredentials.from_service_account_file" in text
        assert '_google_build("sheets"' in text

    def test_sheet_gid_typo_fixed(self):
        """F-8: GOGLE_SHEET_GID was a guaranteed NameError."""
        source = Path(__file__).resolve().parent.parent / "flask_app.py"
        assert "GOGLE_SHEET_GID" not in source.read_text(encoding="utf-8")

    def test_sync_route_reports_error_without_credentials(self, client):
        """The route must fail cleanly, not with a NameError."""
        resp = client.post("/api/sync_sheet")
        assert resp.status_code in (200, 503)
        body = resp.get_json()
        assert body["status"] == "error"
        assert "NameError" not in str(body.get("message", ""))

    def test_approve_does_not_raise_nameerror(self, client, temp_db):
        """F-7 regression: approving must not blow up on the sheet write."""
        temp_db.upsert_applicant({"email": "a@example.com", "name": "A"})
        row = temp_db.get_applicant_by_email("a@example.com")
        resp = client.post(
            "/applicant/%d/approve" % row["id"],
            data={"action": "approve"}, follow_redirects=True,
        )
        assert resp.status_code == 200
        assert temp_db.get_applicant_by_email("a@example.com")["approval_status"] == "Approved"


class TestSecurityDefaults:
    def test_secret_key_not_hardcoded(self):
        """W-4: the old literal key must be gone."""
        source = Path(__file__).resolve().parent.parent / "flask_app.py"
        assert "gayatri-edu-dashboard-2026" not in source.read_text(encoding="utf-8")

    def test_test_recipient_not_hardcoded(self):
        """W-3: the personal address must no longer be in source."""
        source = Path(__file__).resolve().parent.parent / "flask_app.py"
        assert "ain.sharma.abhinav@gmail.com" not in source.read_text(encoding="utf-8")

    def test_test_email_requires_configured_recipient(self, client):
        resp = client.post("/api/send-test-email", json={"template": "result"})
        assert resp.status_code == 400
        assert "GAYATRI_TEST_RECIPIENT" in resp.get_json()["error"]

    def test_no_vbs_or_bat_references(self):
        """F-6: platform-specific launchers must be gone from code."""
        root = Path(__file__).resolve().parent.parent
        for py in root.glob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            assert "launch_hidden" not in text, "%s references launch_hidden" % py.name
            assert "Task Manager" not in text, "%s references Task Manager" % py.name

    def test_no_launcher_scripts_committed(self):
        root = Path(__file__).resolve().parent.parent
        assert list(root.glob("*.vbs")) == []
        assert list(root.glob("*.bat")) == []


class TestEntrypoints:
    def test_flask_app_exposes_main(self):
        """AC-9: console script target must exist."""
        import flask_app
        assert callable(flask_app.main)

    def test_bot_exposes_main(self):
        import bot
        assert callable(bot.main)

    def test_archive_exposes_main_and_is_import_safe(self):
        """The archive script must not run destructively at import."""
        import archive_offer_logs
        assert callable(archive_offer_logs.main)
        assert callable(archive_offer_logs.run)

    def test_doctor_exposes_main(self):
        import doctor
        assert callable(doctor.main)

    def test_port_probe_detects_occupied_port(self):
        import socket
        import flask_app

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert flask_app._port_is_free("127.0.0.1", port) is False
        finally:
            srv.close()
