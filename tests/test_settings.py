"""Tests for persisted discovery settings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodian import settings as settings_mod  # noqa: E402


def test_default_is_scan_all_drives_unrestricted() -> None:
    s = settings_mod.Settings()
    assert s.scan_all_drives is True
    assert s.resolved_roots() is None


def test_included_roots_only_apply_once_scan_all_drives_is_off() -> None:
    s = settings_mod.Settings(scan_all_drives=True, included_roots=("/some/path",))
    assert s.resolved_roots() is None, "roots should be ignored while scan_all_drives is True"


def test_with_included_roots_turns_off_scan_all_drives() -> None:
    s = settings_mod.Settings().with_included_roots(["/Volumes/Work", "/Volumes/Backup"])
    assert s.scan_all_drives is False
    assert s.resolved_roots() == [Path("/Volumes/Work"), Path("/Volumes/Backup")]


def test_empty_included_roots_fails_open_to_unrestricted() -> None:
    """scan_all_drives=False with zero roots configured would otherwise mean
    "search nothing," which reads as the tool being broken rather than
    scoped -- fail open to unrestricted instead of silently finding zero
    projects with no visible reason why."""
    s = settings_mod.Settings(scan_all_drives=False, included_roots=())
    assert s.resolved_roots() is None


def test_with_all_drives_clears_back_to_unrestricted() -> None:
    s = settings_mod.Settings().with_included_roots(["/Volumes/Work"]).with_all_drives()
    assert s.scan_all_drives is True
    assert s.resolved_roots() is None


def test_save_and_load_roundtrip(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "settings_path", lambda: path)

    original = settings_mod.Settings().with_included_roots(["D:/Games", "E:/Dev"])
    settings_mod.save_settings(original)

    loaded = settings_mod.load_settings()
    assert loaded == original
    assert path.is_file()


def test_load_with_no_settings_file_yet_returns_the_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "does-not-exist.json")
    assert settings_mod.load_settings() == settings_mod.Settings()


def test_load_with_a_corrupt_settings_file_is_not_fatal(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(settings_mod, "settings_path", lambda: path)
    assert settings_mod.load_settings() == settings_mod.Settings()
