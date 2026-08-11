"""Persisted, machine-wide discovery settings.

Distinct from a project's `.ueclean.json` (policy.py), which scopes what
gets *cleaned* within one project: this scopes where discovery *looks* at
all, for both the CLI and the GUI. Off by default (search everywhere) --
this exists for the case where "everywhere" is actively wrong, e.g. a slow
or unreliable network/backup drive that happens to be mounted, or simply
wanting only the drive(s) Unreal projects actually live on considered.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path


def settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".config"
    return base / "unreal-custodian" / "settings.json"


@dataclass(frozen=True)
class Settings:
    scan_all_drives: bool = True
    # Only consulted when scan_all_drives is False. Stored as strings (JSON
    # has no Path type); resolved_roots() below is what callers should use.
    included_roots: tuple[str, ...] = ()
    # "Don't show this again" for the GUI's Windows-only "install Everything"
    # notice -- set once the user dismisses it with the checkbox checked.
    # Persisted so it stays dismissed across restarts, not just this session.
    hide_everything_notice: bool = False

    def resolved_roots(self) -> list[Path] | None:
        """None means "no restriction, search everywhere" -- the historical
        default and still what most callers should pass straight through to
        discovery's roots= / scan_root machinery unexamined.

        scan_all_drives=False with zero configured roots would otherwise
        mean "search nothing," which reads as the tool being broken rather
        than scoped -- fail open to unrestricted instead. The GUI/CLI should
        still not let that combination get saved in the first place; this
        is the defensive fallback if it ever does anyway.
        """
        if self.scan_all_drives or not self.included_roots:
            return None
        return [Path(r) for r in self.included_roots]

    def with_all_drives(self) -> "Settings":
        return replace(self, scan_all_drives=True)

    def with_included_roots(self, roots: list[str] | list[Path]) -> "Settings":
        return replace(
            self, scan_all_drives=False, included_roots=tuple(str(r) for r in roots)
        )

    def with_everything_notice_hidden(self) -> "Settings":
        return replace(self, hide_everything_notice=True)


def load_settings() -> Settings:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return Settings(
        scan_all_drives=bool(data.get("scan_all_drives", True)),
        included_roots=tuple(data.get("included_roots", ())),
        hide_everything_notice=bool(data.get("hide_everything_notice", False)),
    )


def save_settings(settings: Settings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_all_drives": settings.scan_all_drives,
        "included_roots": list(settings.included_roots),
        "hide_everything_notice": settings.hide_everything_notice,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
