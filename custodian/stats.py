"""Lifetime tally of space reclaimed -- accumulates across every real clean
run, CLI and GUI alike. Also caches the last-known public community total,
so a launch with no internet still has something to show.

Distinct from a project's `.ueclean.json` (policy.py, what gets cleaned) and
a user's Settings (settings.py, where discovery looks): this is accumulated
history, not configuration, so it lives alongside the run logs (runlog.py)
rather than the config directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def stats_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "state"
    return base / "unreal-custodian" / "stats.json"


def _load_data() -> dict:
    try:
        data = json.loads(stats_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_data(data: dict) -> None:
    path = stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_total_reclaimed() -> int:
    value = _load_data().get("total_bytes_reclaimed", 0)
    return int(value) if isinstance(value, (int, float)) else 0


def record_reclaimed(bytes_reclaimed: int) -> int:
    """Add to the lifetime local total. Returns the new total.

    A no-op (returns the existing total unchanged) for a zero/negative
    amount -- a cancelled or fully-failed run shouldn't be able to move
    this number at all, not even by zero bytes written to disk for nothing.

    Reads the whole file and writes it back with just this one key changed
    -- NOT a blind overwrite -- so this can't silently erase the cached
    public stats (or anything else ever added to this file later).
    """
    if bytes_reclaimed <= 0:
        return load_total_reclaimed()
    data = _load_data()
    total = int(data.get("total_bytes_reclaimed", 0) or 0) + bytes_reclaimed
    data["total_bytes_reclaimed"] = total
    _save_data(data)
    return total


def load_cached_public_stats() -> dict | None:
    """The last-known community totals, from the last successful fetch.

    None if there's never been a successful fetch (fresh install, or every
    attempt so far has failed) -- distinct from a fetch that legitimately
    returned zero, which would be {"total_bytes": 0, "total_reports": 0}.
    """
    cached = _load_data().get("cached_public_stats")
    if not isinstance(cached, dict):
        return None
    total_bytes = cached.get("total_bytes")
    total_reports = cached.get("total_reports")
    if not isinstance(total_bytes, (int, float)) or not isinstance(total_reports, (int, float)):
        return None
    return {"total_bytes": int(total_bytes), "total_reports": int(total_reports)}


def save_cached_public_stats(total_bytes: int, total_reports: int) -> None:
    data = _load_data()
    data["cached_public_stats"] = {
        "total_bytes": int(total_bytes),
        "total_reports": int(total_reports),
    }
    _save_data(data)
