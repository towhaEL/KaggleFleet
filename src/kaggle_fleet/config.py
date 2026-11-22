"""
Configuration and system paths for KaggleFleet.

Everything can be overridden with environment variables:

    KAGGLE_FLEET_DIR            state dir (accounts, jobs, sandboxes)   default ~/.kaggle_fleet
    KAGGLE_FLEET_NOTEBOOKS_DIR  where notebooks are discovered          default ./notebooks or .
    KAGGLE_FLEET_OUTPUTS_DIR    where kernel outputs are downloaded     default ./outputs
    KAGGLE_FLEET_KAGGLE_BIN     path to the `kaggle` executable         default: auto-detect
    KAGGLE_FLEET_SLUG_PREFIX    prefix for slugs that start with digits default "nb"
    KAGGLE_FLEET_QUOTA_RESET    weekly quota reset, "<weekday> HH:MM" UTC default "sat 00:00"
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


# State directory: credential sandboxes, accounts.json, jobs.json, staging.
KAGGLE_FLEET_DIR = _env_path("KAGGLE_FLEET_DIR") or (Path.home() / ".kaggle_fleet").resolve()

ENV_DIR = KAGGLE_FLEET_DIR / "envs"
STAGING_DIR = KAGGLE_FLEET_DIR / "staging"
ACTIVE_SYMLINK = KAGGLE_FLEET_DIR / "active"
ACCOUNTS_FILE = KAGGLE_FLEET_DIR / "accounts.json"
JOBS_FILE = KAGGLE_FLEET_DIR / "jobs.json"

PACKAGE_ROOT = Path(__file__).resolve().parent


def default_notebooks_dir() -> Path:
    """$KAGGLE_FLEET_NOTEBOOKS_DIR, else ./notebooks if it exists, else the current directory."""
    env = _env_path("KAGGLE_FLEET_NOTEBOOKS_DIR")
    if env:
        return env
    cwd = Path.cwd()
    return (cwd / "notebooks").resolve() if (cwd / "notebooks").is_dir() else cwd.resolve()


def default_outputs_dir() -> Path:
    """$KAGGLE_FLEET_OUTPUTS_DIR, else ./outputs."""
    return _env_path("KAGGLE_FLEET_OUTPUTS_DIR") or (Path.cwd() / "outputs").resolve()


# Resolved once at import time; CLI flags and constructor arguments override them.
DEFAULT_NOTEBOOKS_DIR = default_notebooks_dir()
DEFAULT_OUTPUTS_DIR = default_outputs_dir()

SLUG_PREFIX = os.environ.get("KAGGLE_FLEET_SLUG_PREFIX", "nb").strip().lower() or "nb"


def kaggle_bin() -> str:
    """
    Locate the `kaggle` executable.

    Order: $KAGGLE_FLEET_KAGGLE_BIN, PATH, then next to the running Python
    (covers pipx/venv installs where `kaggle` is a dependency but not on PATH).
    Falls back to the bare name so the child process PATH can still resolve it.
    """
    env = os.environ.get("KAGGLE_FLEET_KAGGLE_BIN", "").strip()
    if env:
        return env
    found = shutil.which("kaggle")
    if found:
        return found
    sibling = Path(sys.executable).parent / ("kaggle.exe" if os.name == "nt" else "kaggle")
    if sibling.exists():
        return str(sibling)
    return "kaggle"


def browser_opener() -> str:
    """Command used to open URLs from a sandboxed process (see AccountManager)."""
    return "open" if sys.platform == "darwin" else "xdg-open"


# Supported Kaggle accelerators (user aliases -> Kaggle CLI values)
ACCELERATORS: dict[str, str] = {
    "t4": "NvidiaTeslaT4",
    "t4x2": "NvidiaTeslaT4",
    "nvidiateslat4": "NvidiaTeslaT4",
    "nvidiat4": "NvidiaTeslaT4",
    "p100": "NvidiaTeslaP100",
    "nvidiateslap100": "NvidiaTeslaP100",
    "tpu": "TpuV5E8",
    "tpuv5e8": "TpuV5E8",
    "cpu": "None",
    "none": "None",
}

ACCELERATOR_CHOICES = ["None", "NvidiaTeslaT4", "NvidiaTeslaP100", "TpuV5E8"]

DEFAULT_PUSH_TIMEOUT = 3600
DEFAULT_POLL_INTERVAL = 10


# Kaggle's weekly accelerator quota resets at a fixed moment each week, not on a
# rolling 7-day window. Saturday 00:00 UTC is the commonly reported reset; set
# KAGGLE_FLEET_QUOTA_RESET (e.g. "fri 17:00") if Kaggle's quota page disagrees.
KAGGLE_SESSION_LIMIT_SECONDS = 12 * 3600
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def quota_reset_schedule() -> tuple[int, int, int]:
    """(weekday 0=Mon, hour, minute) in UTC of the weekly quota reset."""
    raw = os.environ.get("KAGGLE_FLEET_QUOTA_RESET", "").strip().lower()
    try:
        day, _, clock = raw.partition(" ")
        hh, _, mm = (clock or "00:00").partition(":")
        weekday, hour, minute = _WEEKDAYS.index(day[:3]), int(hh), int(mm or 0)
        if 0 <= hour < 24 and 0 <= minute < 60:
            return weekday, hour, minute
    except ValueError:
        pass
    return 5, 0, 0


def quota_window(now=None):
    """(start of the current quota week, next reset) as aware UTC datetimes."""
    from datetime import datetime, timedelta, timezone

    now = now or datetime.now(timezone.utc)
    weekday, hour, minute = quota_reset_schedule()
    last = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    last -= timedelta(days=(now.weekday() - weekday) % 7)
    if last > now:
        last -= timedelta(days=7)
    return last, last + timedelta(days=7)


def normalize_accelerator(acc: str | None) -> str:
    """Normalize user input or accelerator string to Kaggle CLI flag."""
    if not acc:
        return "NvidiaTeslaT4"
    cleaned = acc.strip().replace(" ", "").replace("-", "").lower()
    return ACCELERATORS.get(cleaned, acc)


def ensure_dirs(hub_dir: Path | None = None) -> None:
    """Ensure the state directories exist with safe permissions."""
    base = hub_dir or KAGGLE_FLEET_DIR
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    (base / "envs").mkdir(mode=0o700, parents=True, exist_ok=True)
    (base / "staging").mkdir(mode=0o700, parents=True, exist_ok=True)
