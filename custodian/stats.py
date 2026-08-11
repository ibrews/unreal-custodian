"""Lifetime tally of space reclaimed -- accumulates across every real clean
run, CLI and GUI alike.

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


def load_total_reclaimed() -> int:
    path = stats_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    value = data.get("total_bytes_reclaimed", 0)
    return int(value) if isinstance(value, (int, float)) else 0


def record_reclaimed(bytes_reclaimed: int) -> int:
    """Add to the lifetime local total. Returns the new total.

    A no-op (returns the existing total unchanged) for a zero/negative
    amount -- a cancelled or fully-failed run shouldn't be able to move
    this number at all, not even by zero bytes written to disk for nothing.
    """
    if bytes_reclaimed <= 0:
        return load_total_reclaimed()
    path = stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    total = load_total_reclaimed() + bytes_reclaimed
    path.write_text(
        json.dumps({"total_bytes_reclaimed": total}, indent=2) + "\n", encoding="utf-8"
    )
    return total
