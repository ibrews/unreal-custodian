"""Tests for the lifetime local space-reclaimed tally."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodian import stats  # noqa: E402


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
