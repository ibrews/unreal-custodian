"""Tests for the lifetime local space-reclaimed tally."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodian import stats  # noqa: E402


def test_stats_path_is_independent_of_where_the_app_binary_lives() -> None:
    """The whole point of this file is surviving an app update -- replacing
    the .app / .exe with a new build must never touch it. Real stats_path()
    (not monkeypatched), resolved purely from the user's home directory /
    LOCALAPPDATA, never from __file__, sys.executable, or sys._MEIPASS (the
    PyInstaller-frozen temp extraction dir, which is a NEW location on every
    single launch, not just every new build)."""
    path = stats.stats_path()
    module_dir = str(Path(stats.__file__).resolve().parent)
    assert module_dir not in str(path)
    if os.name == "nt":
        assert str(path).startswith(os.environ.get("LOCALAPPDATA", str(Path.home())))
    else:
        assert str(path).startswith(str(Path.home()))


def test_starts_at_zero_with_no_file_yet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(stats, "stats_path", lambda: tmp_path / "stats.json")
    assert stats.load_total_reclaimed() == 0


def test_record_reclaimed_accumulates_across_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(stats, "stats_path", lambda: tmp_path / "stats.json")
    assert stats.record_reclaimed(1000) == 1000
    assert stats.record_reclaimed(500) == 1500
    assert stats.load_total_reclaimed() == 1500


def test_zero_or_negative_amount_is_a_no_op(tmp_path: Path, monkeypatch) -> None:
    """A cancelled or fully-failed run must not move the total, not even by
    writing a redundant zero to disk."""
    path = tmp_path / "stats.json"
    monkeypatch.setattr(stats, "stats_path", lambda: path)
    stats.record_reclaimed(1000)
    assert stats.record_reclaimed(0) == 1000
    assert stats.record_reclaimed(-50) == 1000
    assert stats.load_total_reclaimed() == 1000


def test_corrupt_stats_file_is_not_fatal(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "stats.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(stats, "stats_path", lambda: path)
    assert stats.load_total_reclaimed() == 0
    # And recovers cleanly on the next real write rather than staying stuck.
    assert stats.record_reclaimed(200) == 200


def test_no_cached_public_stats_before_any_successful_fetch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(stats, "stats_path", lambda: tmp_path / "stats.json")
    assert stats.load_cached_public_stats() is None


def test_cached_public_stats_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(stats, "stats_path", lambda: tmp_path / "stats.json")
    stats.save_cached_public_stats(123456, 42)
    assert stats.load_cached_public_stats() == {"total_bytes": 123456, "total_reports": 42}


def test_a_legitimate_zero_fetch_is_distinct_from_never_fetched(tmp_path: Path, monkeypatch) -> None:
    """{"total_bytes": 0, "total_reports": 0} (a real, successful fetch that
    just happens to return zero) must not be confused with "never fetched"
    (None) -- a caller distinguishing "no data yet" from "confirmed zero"
    needs this to actually be distinguishable."""
    monkeypatch.setattr(stats, "stats_path", lambda: tmp_path / "stats.json")
    stats.save_cached_public_stats(0, 0)
    assert stats.load_cached_public_stats() == {"total_bytes": 0, "total_reports": 0}


def test_recording_reclaimed_does_not_clobber_cached_public_stats(tmp_path: Path, monkeypatch) -> None:
    """Real bug this guards against: record_reclaimed() used to overwrite
    the whole stats file, which would have silently erased any cached
    public stats saved before it ran."""
    path = tmp_path / "stats.json"
    monkeypatch.setattr(stats, "stats_path", lambda: path)
    stats.save_cached_public_stats(999, 7)
    stats.record_reclaimed(500)
    assert stats.load_cached_public_stats() == {"total_bytes": 999, "total_reports": 7}
    assert stats.load_total_reclaimed() == 500


def test_caching_public_stats_does_not_clobber_the_local_total(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "stats.json"
    monkeypatch.setattr(stats, "stats_path", lambda: path)
    stats.record_reclaimed(500)
    stats.save_cached_public_stats(999, 7)
    assert stats.load_total_reclaimed() == 500
