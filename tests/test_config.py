"""Configuration loading, precedence, validation and path resolution (AC-5/6/7)."""

import importlib
import sys
from pathlib import Path

import pytest


def _reload_config(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import config
    return importlib.reload(config)


class TestSecrets:
    def test_no_credentials_in_source(self):
        """AC-5: no source file may assign a non-empty password string literal.

        Parsed with ast rather than grepped, so that reading a password from a
        variable (the correct pattern) is not mistaken for a hardcoded one.
        """
        import ast

        root = Path(__file__).resolve().parent.parent
        secret_names = {"password", "passwd", "secret", "api_key", "token"}
        leaked = []

        for py in root.glob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                # dict entries: {"password": "literal"}
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if (isinstance(key, ast.Constant)
                                and isinstance(key.value, str)
                                and key.value.lower() in secret_names
                                and isinstance(value, ast.Constant)
                                and isinstance(value.value, str)
                                and value.value):
                            leaked.append("%s:%d" % (py.name, node.lineno))
                # assignments: PASSWORD = "literal"
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Name)
                                and target.id.lower() in secret_names
                                and isinstance(node.value, ast.Constant)
                                and isinstance(node.value.value, str)
                                and node.value.value):
                            leaked.append("%s:%d" % (py.name, node.lineno))

        assert leaked == [], "hardcoded credentials found at: %s" % leaked

    def test_bot_config_has_no_sender_literals(self):
        """bot_config must not define SENDER_POOL inline any more."""
        root = Path(__file__).resolve().parent.parent
        text = (root / "bot_config.py").read_text(encoding="utf-8")
        assert "SENDER_POOL = [" not in text

    def test_senders_come_from_environment(self, monkeypatch):
        cfg = _reload_config(
            monkeypatch,
            GAYATRI_SENDER_1_EMAIL="dev@example.com",
            GAYATRI_SENDER_1_PASSWORD="test-app-password",
            GAYATRI_SENDER_1_NAME="Dev Tester",
        )
        assert len(cfg.SENDER_POOL) == 1
        assert cfg.SENDER_POOL[0]["email"] == "dev@example.com"
        assert cfg.SENDER_POOL[0]["password"] == "test-app-password"

    def test_sender_numbering_gaps_are_skipped(self, monkeypatch):
        """A removed sender 2 must not hide sender 3."""
        cfg = _reload_config(
            monkeypatch,
            GAYATRI_SENDER_1_EMAIL="one@example.com",
            GAYATRI_SENDER_1_PASSWORD="p1",
            GAYATRI_SENDER_2_EMAIL=None,
            GAYATRI_SENDER_3_EMAIL="three@example.com",
            GAYATRI_SENDER_3_PASSWORD="p3",
        )
        emails = [s["email"] for s in cfg.SENDER_POOL]
        assert emails == ["one@example.com", "three@example.com"]


class TestPaths:
    def test_database_path_is_absolute(self, monkeypatch):
        """AC-7: relative config values resolve against the project directory."""
        cfg = _reload_config(monkeypatch, GAYATRI_DB="gayatri.db")
        assert Path(cfg.DATABASE_FILE).is_absolute()
        assert Path(cfg.DATABASE_FILE).parent == cfg.BASE_DIR

    def test_absolute_override_is_honoured(self, monkeypatch, tmp_path):
        target = tmp_path / "elsewhere.db"
        cfg = _reload_config(monkeypatch, GAYATRI_DB=str(target))
        assert Path(cfg.DATABASE_FILE) == target.resolve()

    def test_paths_independent_of_cwd(self, monkeypatch, tmp_path):
        """AC-7: launching from another directory resolves the same file."""
        import os
        cfg1 = _reload_config(monkeypatch, GAYATRI_DB="gayatri.db")
        first = cfg1.DATABASE_FILE
        original = os.getcwd()
        try:
            os.chdir(tmp_path)
            cfg2 = _reload_config(monkeypatch, GAYATRI_DB="gayatri.db")
            assert cfg2.DATABASE_FILE == first
        finally:
            os.chdir(original)


class TestValidation:
    def test_missing_sheet_id_is_reported(self, monkeypatch):
        """AC-6: the error names the specific variable."""
        cfg = _reload_config(monkeypatch, GAYATRI_SHEET_ID=None)
        problems = cfg.validate(require_sheets=True)
        assert any("GAYATRI_SHEET_ID" in p for p in problems)

    def test_missing_smtp_password_is_reported(self, monkeypatch):
        cfg = _reload_config(
            monkeypatch,
            GAYATRI_SENDER_1_EMAIL="dev@example.com",
            GAYATRI_SENDER_1_PASSWORD=None,
        )
        problems = cfg.validate(require_smtp=True)
        assert any("GAYATRI_SENDER_1_PASSWORD" in p for p in problems)

    def test_clean_config_has_no_problems(self, monkeypatch):
        cfg = _reload_config(
            monkeypatch,
            GAYATRI_SENDER_1_EMAIL="dev@example.com",
            GAYATRI_SENDER_1_PASSWORD="pw",
        )
        assert cfg.validate(require_smtp=True) == []

    def test_inverted_delays_are_reported(self, monkeypatch):
        cfg = _reload_config(
            monkeypatch, GAYATRI_SMTP_MIN_DELAY="90", GAYATRI_SMTP_MAX_DELAY="10"
        )
        assert any("SMTP_MIN_DELAY" in p for p in cfg.validate())

    def test_require_valid_exits_nonzero(self, monkeypatch):
        """AC-6: a misconfigured start exits non-zero rather than raising."""
        cfg = _reload_config(monkeypatch, GAYATRI_SHEET_ID=None)
        with pytest.raises(SystemExit) as exc:
            cfg.require_valid(require_sheets=True)
        assert exc.value.code == 1

    def test_summary_contains_no_password(self, monkeypatch):
        cfg = _reload_config(
            monkeypatch,
            GAYATRI_SENDER_1_EMAIL="dev@example.com",
            GAYATRI_SENDER_1_PASSWORD="super-secret-value",
        )
        assert "super-secret-value" not in repr(cfg.summary())


class TestDefaults:
    def test_dashboard_binds_loopback_by_default(self, monkeypatch):
        """Remedy 4: default must be loopback, never 0.0.0.0."""
        cfg = _reload_config(monkeypatch, GAYATRI_FLASK_HOST=None)
        assert cfg.FLASK_HOST == "127.0.0.1"

    def test_macos_avoids_airplay_port(self, monkeypatch):
        cfg = _reload_config(monkeypatch, GAYATRI_FLASK_PORT=None)
        expected = 5050 if sys.platform == "darwin" else 5000
        assert cfg.FLASK_PORT == expected

    def test_port_override(self, monkeypatch):
        cfg = _reload_config(monkeypatch, GAYATRI_FLASK_PORT="8123")
        assert cfg.FLASK_PORT == 8123

    def test_bad_integer_falls_back(self, monkeypatch):
        cfg = _reload_config(monkeypatch, GAYATRI_FLASK_PORT="not-a-number")
        assert isinstance(cfg.FLASK_PORT, int)
