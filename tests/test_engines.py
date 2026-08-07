"""Tests for engine-install cleanup.

The load-bearing rule: Engine/Binaries in a launcher install is the engine
itself, not build output. Deleting it there does not free rebuildable space --
it destroys a multi-gigabyte install that can only be recovered by
re-downloading. In a source build the same directory is genuinely rebuildable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upj import discovery, policy as policy_mod  # noqa: E402

ALL_KEYS = frozenset(t.key for t in policy_mod.ENGINE_TARGETS)


@pytest.fixture()
def engine(tmp_path: Path) -> Path:
    root = tmp_path / "UE_5.8"
    for rel in (
        "Engine/Build",
        "Engine/Binaries/Mac",
        "Engine/Intermediate/Build",
        "Engine/DerivedDataCache",
        "Engine/Saved/Logs",
        "Engine/Saved/Crashes",
        "Engine/Source/Runtime",
    ):
        (root / rel).mkdir(parents=True)
    (root / "Engine/Build/Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 1}),
        encoding="utf-8",
    )
    return root


def mark_installed(engine: Path) -> None:
    (engine / "Engine/Build/InstalledBuild.txt").touch()


def mark_source(engine: Path) -> None:
    (engine / "Engine/Build/SourceDistribution.txt").touch()


def resolved(engine: Path, installed: bool, keys=ALL_KEYS):
    res = policy_mod.resolve_engine_targets(engine, installed, keys)
    return {p.relative_to(engine).as_posix() for _, p in res.targets}


def skipped(engine: Path, installed: bool, keys=ALL_KEYS):
    res = policy_mod.resolve_engine_targets(engine, installed, keys)
    return {p.relative_to(engine).as_posix(): reason for p, reason in res.skipped}


def test_installed_build_never_gives_up_its_binaries(engine: Path) -> None:
    got = resolved(engine, installed=True)
    assert "Engine/Binaries" not in got
    assert "Engine/Intermediate" not in got
    assert "Engine/Binaries" in skipped(engine, installed=True)


def test_source_build_can_reclaim_binaries_and_intermediate(engine: Path) -> None:
    got = resolved(engine, installed=False)
    assert {"Engine/Binaries", "Engine/Intermediate"} <= got


def test_caches_and_logs_are_safe_on_both_kinds(engine: Path) -> None:
    for installed in (True, False):
        got = resolved(engine, installed)
        assert {"Engine/DerivedDataCache", "Engine/Saved/Logs", "Engine/Saved/Crashes"} <= got


def test_the_expensive_targets_are_off_by_default(engine: Path) -> None:
    """A full engine rebuild is hours; it must never happen without asking."""
    default = policy_mod.resolve_engine_targets(engine, installed_build=False)
    names = {p.relative_to(engine).as_posix() for _, p in default.targets}
    assert "Engine/Binaries" not in names
    assert "Engine/Intermediate" not in names
    assert "Engine/DerivedDataCache" in names


def test_engine_source_is_never_a_target(engine: Path) -> None:
    for installed in (True, False):
        assert "Engine/Source" not in resolved(engine, installed)


def test_installed_build_is_detected_from_the_marker(engine: Path) -> None:
    mark_installed(engine)
    assert discovery._is_installed_build(engine) is True


def test_source_build_is_detected_from_the_marker(engine: Path) -> None:
    mark_source(engine)
    assert discovery._is_installed_build(engine) is False


def test_an_unmarked_engine_is_treated_as_installed(engine: Path) -> None:
    """Guessing wrong in the unsafe direction destroys a precompiled engine."""
    assert discovery._is_installed_build(engine) is True
    assert "Engine/Binaries" not in resolved(engine, discovery._is_installed_build(engine))


def test_a_packaged_game_is_not_an_engine(tmp_path: Path) -> None:
    """Shipped builds carry Build.version, so searching for it finds them all.

    Real case: a Windows machine reported 19 "engines" including two Fortnite
    installs and a WindowsNoEditor packaged build.
    """
    packaged = tmp_path / "MyGame-WindowsNoEditor"
    (packaged / "Engine" / "Build").mkdir(parents=True)
    (packaged / "Engine" / "Content").mkdir(parents=True)
    (packaged / "Engine" / "Build" / "Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 4, "PatchVersion": 4}),
        encoding="utf-8",
    )
    assert discovery._is_engine_install(packaged) is False
    assert discovery._engine_from_root(packaged) is None


def test_a_real_engine_is_recognized(engine: Path) -> None:
    assert discovery._is_engine_install(engine) is True
    found = discovery._engine_from_root(engine)
    assert found is not None and found.version == "5.8.1"


def test_a_binary_install_without_source_is_still_an_engine(tmp_path: Path) -> None:
    """Launcher installs may ship BatchFiles rather than full Source."""
    root = tmp_path / "UE_5.4"
    (root / "Engine" / "Build" / "BatchFiles").mkdir(parents=True)
    (root / "Engine" / "Build" / "Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 4, "PatchVersion": 4}),
        encoding="utf-8",
    )
    assert discovery._is_engine_install(root) is True
