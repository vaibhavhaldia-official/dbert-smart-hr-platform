"""SQLite concurrency hardening (AC-11, AC-12, AC-13)."""

import sqlite3
import threading

import pytest


class TestPragmas:
    def test_wal_is_enabled(self, temp_db):
        """AC-11: journal_mode must be wal on a live database."""
        with temp_db.get_db() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"

    def test_busy_timeout_is_set(self, temp_db):
        """AC-11: busy_timeout must be at least 30 seconds."""
        with temp_db.get_db() as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout >= 30000

    def test_existing_database_migrates_to_wal(self, tmp_path, monkeypatch):
        """AC-11: a pre-Phase-1 database gains WAL without data loss."""
        db_path = tmp_path / "legacy.db"
        legacy = sqlite3.connect(str(db_path))
        legacy.execute("PRAGMA journal_mode=DELETE")
        legacy.execute("CREATE TABLE applicants (id INTEGER PRIMARY KEY, email TEXT UNIQUE)")
        legacy.execute("INSERT INTO applicants (email) VALUES ('legacy@example.com')")
        legacy.commit()
        legacy.close()

        monkeypatch.setenv("GAYATRI_DB", str(db_path))
        import importlib, config, bot_config, database
        importlib.reload(config); importlib.reload(bot_config); importlib.reload(database)

        with database.get_db() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            rows = conn.execute("SELECT email FROM applicants").fetchall()

        assert str(mode).lower() == "wal"
        assert [r[0] for r in rows] == ["legacy@example.com"]


class TestConcurrentWrites:
    def test_no_lost_updates_under_contention(self, temp_db):
        """AC-12: 8 threads x 100 writes, zero lock errors, exact totals."""
        threads_count, per_thread = 8, 100
        campaign_id = temp_db.create_campaign("Concurrency", phase="reminder")
        errors = []

        def worker():
            for _ in range(per_thread):
                try:
                    temp_db.increment_campaign_sent(campaign_id)
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], "unhandled errors: %r" % errors[:3]
        stats = temp_db.get_campaign_stats(campaign_id)
        assert stats["total_sent"] == threads_count * per_thread

    def test_reader_not_blocked_during_writes(self, temp_db):
        """AC-12: a reader stays consistent throughout a write burst."""
        campaign_id = temp_db.create_campaign("ReaderTest", phase="reminder")
        stop = threading.Event()
        read_errors = []
        reads = []

        def reader():
            while not stop.is_set():
                try:
                    reads.append(temp_db.get_campaign_stats(campaign_id))
                except Exception as exc:
                    read_errors.append(exc)

        def writer():
            for _ in range(150):
                temp_db.increment_campaign_sent(campaign_id)

        r = threading.Thread(target=reader)
        r.start()
        writers = [threading.Thread(target=writer) for _ in range(3)]
        for t in writers:
            t.start()
        for t in writers:
            t.join()
        stop.set()
        r.join(timeout=10)

        assert read_errors == [], "reader errors: %r" % read_errors[:3]
        assert len(reads) > 0
        assert temp_db.get_campaign_stats(campaign_id)["total_sent"] == 450

    def test_concurrent_log_writes(self, temp_db):
        """AC-12: parallel email_log inserts all land."""
        errors = []

        def worker(n):
            for i in range(40):
                try:
                    temp_db.log_email_send(
                        to_email="user%d_%d@example.com" % (n, i),
                        from_email="sender@example.com",
                        subject="Subject",
                        phase="result",
                    )
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert temp_db.get_email_log_stats()["total"] == 240


class TestUpsertRace:
    def test_concurrent_upsert_yields_one_row(self, temp_db):
        """AC-13: the same email upserted in parallel produces exactly one row."""
        email = "race@example.com"
        errors = []

        def worker(n):
            for i in range(25):
                try:
                    temp_db.upsert_applicant({
                        "email": email,
                        "name": "Worker %d-%d" % (n, i),
                        "internship": "Data Analyst",
                    })
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], "upsert errors: %r" % errors[:3]
        with temp_db.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM applicants WHERE email = ?", (email,)
            ).fetchone()[0]
        assert count == 1

    def test_timestamps_preserved_when_not_supplied(self, temp_db):
        """AC-13: existing timestamps survive a later upsert that omits them."""
        email = "keep@example.com"
        temp_db.upsert_applicant({"email": email, "name": "First"})
        temp_db.update_confirmation_sent_at(email, "2026-01-01 10:00:00")
        temp_db.update_email_sent_at(email, "2026-01-02 11:00:00")

        temp_db.upsert_applicant({"email": email, "name": "Second", "college": "ABC"})

        row = temp_db.get_applicant_by_email(email)
        assert row["confirmation_sent_at"] == "2026-01-01 10:00:00"
        assert row["email_sent_at"] == "2026-01-02 11:00:00"
        assert row["name"] == "Second"
        assert row["college"] == "ABC"

    def test_explicit_timestamp_overwrites(self, temp_db):
        """A caller that does supply a timestamp still wins."""
        email = "explicit@example.com"
        temp_db.upsert_applicant({"email": email, "name": "X"})
        temp_db.update_confirmation_sent_at(email, "2026-01-01 10:00:00")
        temp_db.upsert_applicant({
            "email": email, "name": "X",
            "confirmation_sent_at": "2026-05-05 05:00:00",
        })
        row = temp_db.get_applicant_by_email(email)
        assert row["confirmation_sent_at"] == "2026-05-05 05:00:00"

    def test_upsert_returns_stable_id(self, temp_db):
        """The same email must keep its row id across upserts."""
        email = "stable@example.com"
        first = temp_db.upsert_applicant({"email": email, "name": "A"})
        second = temp_db.upsert_applicant({"email": email, "name": "B"})
        assert first == second


class TestRetryDecorator:
    def test_retries_then_succeeds(self):
        import database
        calls = {"n": 0}

        @database.retry_on_locked(attempts=4, base=0.001)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

    def test_non_lock_errors_propagate_immediately(self):
        import database
        calls = {"n": 0}

        @database.retry_on_locked(attempts=4, base=0.001)
        def broken():
            calls["n"] += 1
            raise sqlite3.OperationalError("no such table: nope")

        with pytest.raises(sqlite3.OperationalError):
            broken()
        assert calls["n"] == 1
