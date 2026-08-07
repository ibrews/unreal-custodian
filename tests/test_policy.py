"""Tests for the safety boundary.

These are the tests that matter. Everything else in this project is a
convenience; `policy.resolve_targets` is the only thing standing between a
user's work and the Trash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upj import policy as policy_mod  # noqa: E402


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "MyProject"
    for rel in (
        "Content/Maps",
        "Source/MyProject",
        "Config",
        "Intermediate/Build",
        "Binaries/Mac",
        "Saved/Logs",
        "Saved/SaveGames",
        "Saved/Autosaves",
        "Saved/Cooked",
        "Plugins/Foo/Intermediate",
        "Plugins/Foo/Source",
        "DerivedDataCache",
    ):
        (root / rel).mkdir(parents=True)
    (root / "MyProject.uproject").write_text("{}", encoding="utf-8")
    return root


def resolved_paths(project: Path, policy: policy_mod.Policy, is_cpp: bool, age: float | None):
    return {
        p.relative_to(project).as_posix()
        for _, p in policy_mod.resolve_targets(project, policy, is_cpp, age).targets
    }


def test_never_touches_authored_content(project: Path) -> None:
    got = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=9999)
    for protected in ("Content", "Source", "Config", "Plugins", "Plugins/Foo/Source"):
        assert protected not in got


def test_never_touches_save_games(project: Path) -> None:
    got = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=9999)
    assert not any("SaveGames" in path for path in got)


def test_saved_itself_is_never_a_target(project: Path) -> None:
    """Only named subdirectories of Saved are removable, never the whole tree."""
    got = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=9999)
    assert "Saved" not in got
    assert "Saved/Cooked" in got


def test_reclaims_the_obvious_ones(project: Path) -> None:
    got = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=9999)
    assert {"Intermediate", "DerivedDataCache", "Plugins/Foo/Intermediate"} <= got


def test_cpp_projects_keep_binaries_by_default(project: Path) -> None:
    """A C++ rebuild is expensive; Blueprint-only binaries regenerate on open."""
    cpp = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=True, age=9999)
    blueprint = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=9999)
    assert "Binaries" not in cpp
    assert "Binaries" in blueprint


def test_autosaves_are_age_gated_well_beyond_the_project_threshold(project: Path) -> None:
    """After a crash, Autosaves is sometimes the only copy of unsaved work."""
    recent = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=30)
    ancient = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=9999)
    assert "Saved/Autosaves" not in recent
    assert "Saved/Autosaves" in ancient


def test_unknown_age_never_unlocks_age_gated_targets(project: Path) -> None:
    got = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=None)
    assert "Saved/Autosaves" not in got


def test_saved_config_is_opt_in(project: Path) -> None:
    (project / "Saved" / "Config").mkdir()
    default = resolved_paths(project, policy_mod.DEFAULT_POLICY, is_cpp=False, age=9999)
    assert "Saved/Config" not in default

    opted_in = policy_mod.DEFAULT_POLICY.with_overrides(
        {"targets": ["saved_config"]}
    )
    assert "Saved/Config" in resolved_paths(project, opted_in, is_cpp=False, age=9999)


def test_escaping_the_project_root_is_refused(project: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        policy_mod._assert_deletable(project, tmp_path / "elsewhere")


def test_symlinked_plugin_is_skipped_not_followed(project: Path, tmp_path: Path) -> None:
    """A plugin symlinked to another checkout must not be reclaimed through.

    Real case: Plugins/ECABridge -> /Users/Shared/GH/ECABridge. Deleting its
    Intermediate would reclaim space from a repository the user never named.
    """
    external = tmp_path / "OtherRepo"
    (external / "Intermediate").mkdir(parents=True)

    linked = project / "Plugins" / "Linked"
    linked.symlink_to(external, target_is_directory=True)

    resolution = policy_mod.resolve_targets(
        project, policy_mod.DEFAULT_POLICY, is_cpp=False, age_days=9999
    )
    assert not any("OtherRepo" in str(p) for _, p in resolution.targets)
    assert any("Linked" in str(p) for p, _ in resolution.skipped)
    # The rest of the project is still processed.
    assert any(p.name == "Intermediate" and "Plugins" not in p.parts
               for _, p in resolution.targets)


def test_one_bad_target_does_not_abort_the_project(project: Path, tmp_path: Path) -> None:
    linked = project / "Plugins" / "Bad"
    linked.symlink_to(tmp_path / "nowhere" / "deep", target_is_directory=True)
    resolution = policy_mod.resolve_targets(
        project, policy_mod.DEFAULT_POLICY, is_cpp=False, age_days=9999
    )
    assert resolution.targets  # still found the normal ones


def test_project_config_overrides_global_defaults(project: Path) -> None:
    (project / policy_mod.CONFIG_FILENAME).write_text(
        json.dumps({"min_age_days": 90, "targets": ["intermediate"]}), encoding="utf-8"
    )
    loaded = policy_mod.load_policy(project)
    assert loaded.min_age_days == 90
    assert resolved_paths(project, loaded, is_cpp=False, age=9999) == {"Intermediate"}


def test_opting_a_project_out_is_honored(project: Path) -> None:
    (project / policy_mod.CONFIG_FILENAME).write_text(
        json.dumps({"enabled": False}), encoding="utf-8"
    )
    assert policy_mod.load_policy(project).enabled is False


def test_unknown_target_key_is_rejected_loudly(project: Path) -> None:
    (project / policy_mod.CONFIG_FILENAME).write_text(
        json.dumps({"targets": ["intermediate", "Content"]}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        policy_mod.load_policy(project)


def test_unwritable_directory_is_skipped_not_attempted(project: Path, monkeypatch) -> None:
    """Projects copied from another machine are routinely owned by someone else.

    Windows has no getuid(), so the owner hint must be optional -- reaching for
    it unconditionally crashed the whole scan there.
    """
    blocked = project / "Intermediate"
    monkeypatch.setattr(
        policy_mod, "_is_writable", lambda d: str(d) != str(blocked)
    )

    resolution = policy_mod.resolve_targets(
        project, policy_mod.DEFAULT_POLICY, is_cpp=False, age_days=9999
    )
    assert not any(p == blocked for _, p in resolution.targets)
    reasons = {p: reason for p, reason in resolution.skipped}
    assert blocked in reasons
    assert "not writable" in reasons[blocked]
    # Everything else in the project is still planned.
    assert any(p.name == "DerivedDataCache" for _, p in resolution.targets)


def test_owner_hint_is_optional(project: Path, monkeypatch) -> None:
    """No getuid() (Windows) must degrade to no hint, never an AttributeError."""
    monkeypatch.delattr(policy_mod.os, "getuid", raising=False)
    assert policy_mod._owner_hint(project) == ""
