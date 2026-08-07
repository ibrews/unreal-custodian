"""How long ago was this project genuinely worked on?

Two independent signals, because filesystem mtimes lie. Copying a project
directory, restoring it from a backup, or syncing it through cloud storage
rewrites every mtime, making a long-dead project look like it was touched
this morning. That failure is safe (the tool declines to clean) but it is
permanent -- such a project is never eligible again.

So a second, copy-proof signal is read alongside it: Unreal's rotated editor
logs encode their timestamp in the *filename*, which survives a copy intact.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ProjectName-backup-2026.08.07-11.28.43.log
_LOG_STAMP = re.compile(
    r"-backup-(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})\.log$", re.IGNORECASE
)

_SOURCE_DIRS = ("Content", "Source", "Config")


@dataclass(frozen=True)
class Freshness:
    content_mtime: float | None  # newest authored file, by filesystem mtime
    last_session: float | None  # newest rotated editor log, by embedded timestamp

    @property
    def best(self) -> float | None:
        """Most recent evidence of activity from either signal."""
        stamps = [s for s in (self.content_mtime, self.last_session) if s is not None]
        return max(stamps) if stamps else None

    @property
    def age_days(self) -> float | None:
        newest = self.best
        if newest is None:
            return None
        return (datetime.now(timezone.utc).timestamp() - newest) / 86400.0

    @property
    def mtimes_look_rewritten(self) -> bool:
        """Filesystem mtime is much newer than any real editor session.

        Signature of a bulk copy, restore, or cloud resync. Advisory only --
        it is a reason to show the user a note, not a reason to delete.
        """
        if self.content_mtime is None or self.last_session is None:
            return False
        return (self.content_mtime - self.last_session) > 30 * 86400.0


def _newest_mtime(root: Path) -> float | None:
    newest: float | None = None
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _: None):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            try:
                mtime = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
        # Directories under Content can be enormous; this walk is the reason
        # sizing and freshness both belong off the UI thread.
    return newest


def _newest_log_session(saved_logs: Path) -> float | None:
    """Newest timestamp encoded in a rotated log *filename*."""
    newest: float | None = None
    try:
        entries = list(saved_logs.iterdir())
    except OSError:
        return None
    for entry in entries:
        match = _LOG_STAMP.search(entry.name)
        if not match:
            continue
        try:
            stamp = datetime(
                *(int(g) for g in match.groups()), tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            continue
        if newest is None or stamp > newest:
            newest = stamp
    return newest


def measure(project_root: Path) -> Freshness:
    content_mtime: float | None = None
    for name in _SOURCE_DIRS:
        directory = project_root / name
        if not directory.is_dir():
            continue
        found = _newest_mtime(directory)
        if found is not None and (content_mtime is None or found > content_mtime):
            content_mtime = found

    return Freshness(
        content_mtime=content_mtime,
        last_session=_newest_log_session(project_root / "Saved" / "Logs"),
    )
