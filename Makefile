# GayatriBot — developer tasks. Works on Windows, macOS and Linux.
PY ?= python3

.PHONY: help install install-dev run bot archive doctor test test-cov lint clean

help:
	@echo "install      Install runtime dependencies"
	@echo "install-dev  Install runtime + test dependencies"
	@echo "run          Start the admin dashboard"
	@echo "bot          Start the sheet-polling email bot"
	@echo "archive      Archive offer-letter state (needs --yes to act)"
	@echo "doctor       Preflight check: is this machine ready?"
	@echo "test         Run the test suite"
	@echo "test-cov     Run tests with a coverage report"
	@echo "clean        Remove caches and build artifacts"

install:
	$(PY) -m pip install -r requirements.txt

install-dev:
	$(PY) -m pip install -r requirements-dev.txt

run:
	$(PY) flask_app.py

bot:
	$(PY) bot.py

archive:
	$(PY) archive_offer_logs.py

doctor:
	$(PY) doctor.py

test:
	$(PY) -m pytest tests/ -q

test-cov:
	$(PY) -m pytest tests/ --cov=. --cov-report=term-missing

lint:
	$(PY) -m compileall -q .

clean:
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	$(PY) -c "import shutil; shutil.rmtree('.pytest_cache', ignore_errors=True)"
