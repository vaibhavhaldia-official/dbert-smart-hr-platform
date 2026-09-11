"""
send_control.py — Per-script and global email send pause/resume.

Uses sentinel files in the project root as pause flags. Each sending script
has its own flag plus there is a global master flag.

Files used:
  .send_paused       — global: pauses ALL sending (bot + campaigns + offers)
  .bot_paused        — pauses bot.py only (result/acknowledgement emails)
  .campaigns_paused  — pauses smtp_sender.py only (reminder campaigns)
  .offers_paused     — pauses offer_letter_sender.py only (offer letters)

Logic in each script:
  1. Check global pause first → if active, skip regardless of per-script flag
  2. Check per-script flag → if active, skip
  3. Otherwise proceed with sending

A script is blocked when:
  global pause OR its own per-script pause

This allows independent control:
  - Pause bot, let campaigns run          → set .bot_paused
  - Pause campaigns, let bot run          → set .campaigns_paused
  - Pause offers, let bot+campaigns run   → set .offers_paused
  - Pause everything                      → set .send_paused (global)
"""

import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# File paths
_GLOBAL_PAUSE_FILE = os.path.join(BASE_DIR, ".send_paused")
_BOT_PAUSE_FILE = os.path.join(BASE_DIR, ".bot_paused")
_CAMPAIGNS_PAUSE_FILE = os.path.join(BASE_DIR, ".campaigns_paused")
_OFFERS_PAUSE_FILE = os.path.join(BASE_DIR, ".offers_paused")


# ============================================================
# GLOBAL (master) pause — affects ALL scripts
# ============================================================

def is_globally_paused():
    """Check if the global send pause is active."""
    return os.path.exists(_GLOBAL_PAUSE_FILE)


def set_globally_paused(paused: bool):
    """Set or clear the global send pause."""
    if paused:
        with open(_GLOBAL_PAUSE_FILE, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        if os.path.exists(_GLOBAL_PAUSE_FILE):
            os.remove(_GLOBAL_PAUSE_FILE)


def get_global_pause_info():
    """Return (is_paused: bool, since: str|None) for the global pause."""
    if is_globally_paused():
        try:
            with open(_GLOBAL_PAUSE_FILE) as f:
                return True, f.read().strip()
        except Exception:
            return True, None
    return False, None


# ============================================================
# Per-script pause controls
# ============================================================

def is_bot_paused():
    """Check if bot.py (result emails) is paused."""
    return os.path.exists(_BOT_PAUSE_FILE)


def set_bot_paused(paused: bool):
    """Pause/resume bot.py only."""
    if paused:
        with open(_BOT_PAUSE_FILE, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        if os.path.exists(_BOT_PAUSE_FILE):
            os.remove(_BOT_PAUSE_FILE)


def get_bot_pause_info():
    """Return (is_paused: bool, since: str|None) for bot.py."""
    if is_bot_paused():
        try:
            with open(_BOT_PAUSE_FILE) as f:
                return True, f.read().strip()
        except Exception:
            return True, None
    return False, None


def is_campaigns_paused():
    """Check if smtp_sender.py (reminder campaigns) is paused."""
    return os.path.exists(_CAMPAIGNS_PAUSE_FILE)


def set_campaigns_paused(paused: bool):
    """Pause/resume smtp_sender.py only."""
    if paused:
        with open(_CAMPAIGNS_PAUSE_FILE, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        if os.path.exists(_CAMPAIGNS_PAUSE_FILE):
            os.remove(_CAMPAIGNS_PAUSE_FILE)


def get_campaigns_pause_info():
    """Return (is_paused: bool, since: str|None) for smtp_sender.py."""
    if is_campaigns_paused():
        try:
            with open(_CAMPAIGNS_PAUSE_FILE) as f:
                return True, f.read().strip()
        except Exception:
            return True, None
    return False, None


def is_offers_paused():
    """Check if offer_letter_sender.py is paused."""
    return os.path.exists(_OFFERS_PAUSE_FILE)


def set_offers_paused(paused: bool):
    """Pause/resume offer_letter_sender.py only."""
    if paused:
        with open(_OFFERS_PAUSE_FILE, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        if os.path.exists(_OFFERS_PAUSE_FILE):
            os.remove(_OFFERS_PAUSE_FILE)


def get_offers_pause_info():
    """Return (is_paused: bool, since: str|None) for offer_letter_sender.py."""
    if is_offers_paused():
        try:
            with open(_OFFERS_PAUSE_FILE) as f:
                return True, f.read().strip()
        except Exception:
            return True, None
    return False, None


# ============================================================
# Convenience: is sending blocked for a given script?
# ============================================================

def is_blocked(script: str) -> bool:
    """
    Return True if the given script should pause sending.

    script must be one of: 'bot', 'campaigns', 'offers'

    A script is blocked when:
      - Global pause is active (master override), OR
      - Its own per-script pause is active
    """
    if is_globally_paused():
        return True
    if script == "bot":
        return is_bot_paused()
    if script == "campaigns":
        return is_campaigns_paused()
    if script == "offers":
        return is_offers_paused()
    raise ValueError(f"Unknown script: {script!r}. Use 'bot', 'campaigns', or 'offers'.")


def get_all_status() -> dict:
    """Return a dict with the pause status of every control."""
    g_paused, g_since = get_global_pause_info()
    b_paused, b_since = get_bot_pause_info()
    c_paused, c_since = get_campaigns_pause_info()
    o_paused, o_since = get_offers_pause_info()
    return {
        "global": {"paused": g_paused, "since": g_since},
        "bot": {"paused": b_paused, "since": b_since},
        "campaigns": {"paused": c_paused, "since": c_since},
        "offers": {"paused": o_paused, "since": o_since},
    }
