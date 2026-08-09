"""A plain-text record of what a clean run actually did.

Found the hard way, 2026-08-09: a real cleanup run hit several failures, the
GUI's summary dialog truncated the list to the first 8 and showed nothing
else, and the moment the user dismissed it the detail was gone -- there was
nowhere to go back and check what happened. This is not `logging`-module
machinery; it is one append-only file per run, human-readable, with every
failure in full (never truncated), so "what actually happened" survives
closing the dialog.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


def log_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "state"
    directory = base / "unreal-custodian" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class RunLog:
    """Call `write` as things happen; `path` is final once `close()` returns."""

    def __init__(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = log_dir() / f"clean-{stamp}.log"
        self._lines: list[str] = [f"Unreal Custodian clean run -- {stamp}"]

    def write(self, line: str = "") -> None:
        self._lines.append(line)

    def close(self) -> Path:
        self.path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")
        return self.path
