"""Tests for `clean --apply`'s eligibility gate.

The load-bearing rule this covers: age is a default for an unattended,
whole-machine sweep, not an absolute block on a project a user explicitly
named. `--only <name>` bypasses it the same way picking a row in the GUI
and clicking Clean Selected does; the opted-out and nothing-to-reclaim
checks stay absolute either way.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodian import policy as policy_mod  # noqa: E402
from custodian.cli import _is_eligible_to_clean  # noqa: E402
from custodian.discovery import Project  # noqa: E402
from custodian.freshness import Freshness  # noqa: E402
from custodian.sizing import ProjectReport, TargetSize  # noqa: E402


def make_report(
    tmp_path: Path,
    *,
    age_days: float | None = 30,
    enabled: bool = True,
    reclaimable: bool = True,
    error: str | None = None,
) -> ProjectReport:
    root = tmp_path / "SomeProject"
    root.mkdir(exist_ok=True)
    uproject = root / "SomeProject.uproject"
    uproject.write_text("{}", encoding="utf-8")
    project = Project(uproject=uproject, engine_association="5.8")

    policy = policy_mod.DEFAULT_POLICY.with_overrides({"enabled": enabled})
    content_mtime = time.time() - age_days * 86400 if age_days is not None else None
    freshness = Freshness(content_mtime=content_mtime, last_session=None)

    sizes = []
    if reclaimable:
        target = policy_mod.TARGETS_BY_KEY["intermediate"]
        sizes = [TargetSize(target=target, path=root / "Intermediate", bytes=1024)]

    return ProjectReport(project, policy, freshness, sizes=sizes, error=error)


def test_recent_project_excluded_from_an_automatic_sweep(tmp_path: Path) -> None:
    report = make_report(tmp_path, age_days=3)  # policy default is 14
    assert not _is_eligible_to_clean(report, explicit=False)


def test_recent_project_included_when_explicitly_named(tmp_path: Path) -> None:
    """The actual bug this fixes: selecting/naming a project explicitly must
    not be silently dropped just because it was touched recently."""
    report = make_report(tmp_path, age_days=3)
    assert _is_eligible_to_clean(report, explicit=True)


def test_old_enough_project_is_eligible_either_way(tmp_path: Path) -> None:
    report = make_report(tmp_path, age_days=30)
    assert _is_eligible_to_clean(report, explicit=False)
    assert _is_eligible_to_clean(report, explicit=True)


def test_opted_out_project_stays_excluded_even_when_named_explicitly(tmp_path: Path) -> None:
    """--only bypasses the age default, not a project's own .ueclean.json opt-out."""
    report = make_report(tmp_path, age_days=30, enabled=False)
    assert not _is_eligible_to_clean(report, explicit=True)


def test_nothing_reclaimable_stays_excluded_even_when_named_explicitly(tmp_path: Path) -> None:
    report = make_report(tmp_path, age_days=30, reclaimable=False)
    assert not _is_eligible_to_clean(report, explicit=True)


def test_scan_error_stays_excluded_even_when_named_explicitly(tmp_path: Path) -> None:
    report = make_report(tmp_path, age_days=30, error="bad .ueclean.json")
    assert not _is_eligible_to_clean(report, explicit=True)


def test_unknown_age_behaves_like_too_recent_but_still_respects_explicit(
    tmp_path: Path,
) -> None:
    """No content/log signal at all (age_days is None) makes age_eligible
    False, same as a genuinely-recent project -- excluded from an automatic
    sweep, but --only still bypasses it the same way."""
    report = make_report(tmp_path, age_days=None)
    assert not _is_eligible_to_clean(report, explicit=False)
    assert _is_eligible_to_clean(report, explicit=True)
