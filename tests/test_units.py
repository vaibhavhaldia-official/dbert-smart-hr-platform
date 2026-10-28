"""Unit coverage for business logic that must not change in Phase 1 (AC-14)."""

import importlib
from datetime import datetime

import pytest


class TestEnrollmentFilter:
    def test_matching_is_case_insensitive(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "enrollment.csv"
        csv_path.write_text("email,name\nEnrolled@Example.COM,Someone\n", encoding="utf-8")
        import enrollment_filter
        importlib.reload(enrollment_filter)
        enrollment_filter.load_enrollments(str(csv_path))
        assert enrollment_filter.is_enrolled("enrolled@example.com")
        assert enrollment_filter.is_enrolled("ENROLLED@EXAMPLE.COM")
        assert not enrollment_filter.is_enrolled("other@example.com")

    def test_missing_file_processes_everyone(self, tmp_path, monkeypatch):
        """A missing CSV must not silently block all sending."""
        import enrollment_filter
        importlib.reload(enrollment_filter)
        enrollment_filter.load_enrollments(str(tmp_path / "absent.csv"))
        assert not enrollment_filter.is_enrolled("anyone@example.com")

    def test_whitespace_is_trimmed(self, tmp_path):
        csv_path = tmp_path / "e.csv"
        csv_path.write_text("email\n  spaced@example.com  \n", encoding="utf-8")
        import enrollment_filter
        importlib.reload(enrollment_filter)
        enrollment_filter.load_enrollments(str(csv_path))
        assert enrollment_filter.is_enrolled("spaced@example.com")


class TestSendControl:
    def test_pause_and_resume_roundtrip(self, monkeypatch, tmp_path):
        import send_control
        importlib.reload(send_control)
        monkeypatch.setattr(send_control, "_BOT_PAUSE_FILE", str(tmp_path / ".bot_paused"))
        assert not send_control.is_bot_paused()
        send_control.set_bot_paused(True)
        assert send_control.is_bot_paused()
        send_control.set_bot_paused(False)
        assert not send_control.is_bot_paused()

    def test_global_pause_blocks_every_script(self, monkeypatch, tmp_path):
        import send_control
        importlib.reload(send_control)
        monkeypatch.setattr(send_control, "_GLOBAL_PAUSE_FILE", str(tmp_path / ".send_paused"))
        monkeypatch.setattr(send_control, "_BOT_PAUSE_FILE", str(tmp_path / ".bot_paused"))
        monkeypatch.setattr(send_control, "_CAMPAIGNS_PAUSE_FILE", str(tmp_path / ".c"))
        monkeypatch.setattr(send_control, "_OFFERS_PAUSE_FILE", str(tmp_path / ".o"))
        send_control.set_globally_paused(True)
        assert send_control.is_blocked("bot")
        assert send_control.is_blocked("campaigns")
        assert send_control.is_blocked("offers")
        send_control.set_globally_paused(False)

    def test_unknown_script_rejected(self, monkeypatch, tmp_path):
        """Isolate pause files so a stale global flag cannot short-circuit."""
        import send_control
        importlib.reload(send_control)
        monkeypatch.setattr(send_control, "_GLOBAL_PAUSE_FILE", str(tmp_path / ".g"))
        monkeypatch.setattr(send_control, "_BOT_PAUSE_FILE", str(tmp_path / ".b"))
        monkeypatch.setattr(send_control, "_CAMPAIGNS_PAUSE_FILE", str(tmp_path / ".c"))
        monkeypatch.setattr(send_control, "_OFFERS_PAUSE_FILE", str(tmp_path / ".o"))
        with pytest.raises(ValueError):
            send_control.is_blocked("nonsense")


class TestRateLimitBuckets:
    def test_hour_bucket_format(self, temp_db):
        bucket = temp_db.get_hour_bucket(datetime(2026, 9, 10, 14, 30))
        assert bucket == "2026-09-10T14"

    def test_day_bucket_format(self, temp_db):
        assert temp_db.get_day_bucket(datetime(2026, 9, 10, 14, 30)) == "2026-09-10"

    def test_counts_increment_per_bucket(self, temp_db):
        bucket = temp_db.get_hour_bucket()
        assert temp_db.get_sender_count("s@example.com", bucket) == 0
        temp_db.increment_sender_count("s@example.com", bucket)
        temp_db.increment_sender_count("s@example.com", bucket)
        assert temp_db.get_sender_count("s@example.com", bucket) == 2

    def test_daily_counts_are_independent(self, temp_db):
        day = temp_db.get_day_bucket()
        temp_db.increment_sender_daily_count("s@example.com", day)
        assert temp_db.get_sender_daily_count("s@example.com", day) == 1
        assert temp_db.get_sender_daily_count("other@example.com", day) == 0


class TestApplicantQueries:
    def test_offer_targets_exclude_already_sent(self, temp_db):
        temp_db.upsert_applicant({"email": "a@example.com", "name": "A"})
        temp_db.update_confirmation_sent_at("a@example.com", "2026-01-01 00:00:00")
        assert any(t["email"] == "a@example.com" for t in temp_db.get_offer_letter_targets())
        temp_db.update_offer_sent_at("a@example.com", "2026-01-02 00:00:00")
        assert not any(t["email"] == "a@example.com" for t in temp_db.get_offer_letter_targets())

    def test_offer_targets_require_confirmation(self, temp_db):
        temp_db.upsert_applicant({"email": "b@example.com", "name": "B"})
        assert not any(t["email"] == "b@example.com" for t in temp_db.get_offer_letter_targets())

    def test_dashboard_stats_counts(self, temp_db):
        temp_db.upsert_applicant({"email": "c@example.com", "name": "C"})
        temp_db.upsert_applicant({"email": "d@example.com", "name": "D"})
        temp_db.update_approval_status("d@example.com", "Approved")
        stats = temp_db.get_dashboard_stats()
        assert stats["total_applicants"] == 2
        assert stats["approved"] == 1
        assert stats["pending"] == 1

    def test_filter_by_domain(self, temp_db):
        temp_db.upsert_applicant({"email": "e@example.com", "name": "E", "internship": "Data Analyst"})
        temp_db.upsert_applicant({"email": "f@example.com", "name": "F", "internship": "Python Automation"})
        rows = temp_db.get_all_applicants(filters={"domain": "Data Analyst"})
        assert [r["email"] for r in rows] == ["e@example.com"]


class TestCTAs:
    def test_defaults_are_seeded(self, temp_db):
        assert len(temp_db.get_all_ctas(email_type="result")) >= 1

    def test_create_and_soft_delete(self, temp_db):
        cta_id = temp_db.create_cta("Apply", "https://example.com", email_type="result")
        labels = [c["label"] for c in temp_db.get_all_ctas(email_type="result")]
        assert "Apply" in labels
        temp_db.delete_cta(cta_id)
        labels = [c["label"] for c in temp_db.get_all_ctas(email_type="result")]
        assert "Apply" not in labels

    def test_update_rejects_unknown_fields(self, temp_db):
        cta_id = temp_db.create_cta("X", "https://example.com")
        temp_db.update_cta(cta_id, label="Y", bogus_column="ignored")
        assert temp_db.get_cta_by_id(cta_id)["label"] == "Y"


class TestCSVRoundTrip:
    def test_export_then_import(self, temp_db, tmp_path):
        temp_db.upsert_applicant({
            "email": "g@example.com", "name": "G",
            "internship": "Data Analyst", "college": "XYZ",
        })
        rows = temp_db.get_master_csv_data()
        assert len(rows) == 1

        import csv
        csv_path = tmp_path / "master.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["email", "name", "internship_domain"])
            writer.writeheader()
            writer.writerow({
                "email": "h@example.com", "name": "H",
                "internship_domain": "Full Stack Development",
            })

        result = temp_db.import_master_csv(str(csv_path))
        assert result["inserted"] == 1
        assert temp_db.get_applicant_by_email("h@example.com")["name"] == "H"

    def test_import_skips_rows_without_email(self, temp_db, tmp_path):
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("email,name\n,Nameless\n", encoding="utf-8")
        result = temp_db.import_master_csv(str(csv_path))
        assert result["skipped"] == 1
        assert result["inserted"] == 0


class TestOfferDateParsing:
    def test_blank_start_date_has_a_default(self):
        from offer_letter_sender import _parse_start_date
        parsed = _parse_start_date("")
        datetime.strptime(parsed, "%d-%b-%Y")  # must be parseable

    def test_known_format_roundtrips(self):
        from offer_letter_sender import _parse_start_date
        parsed = _parse_start_date("2026-10-01 09:00:00")
        assert datetime.strptime(parsed, "%d-%b-%Y").year >= 2026
