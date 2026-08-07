"""Tests for engine and project discovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodian import discovery  # noqa: E402


def make_engine(root: Path, major: int, minor: int, patch: int = 0) -> Path:
    build = root / "Engine" / "Build"
    build.mkdir(parents=True)
    # Without build machinery this is a packaged game, not an engine.
    (build / "BatchFiles").mkdir()
    (root / "Engine" / "Source").mkdir()
    (build / "Build.version").write_text(
        json.dumps({"MajorVersion": major, "MinorVersion": minor, "PatchVersion": patch}),
        encoding="utf-8",
    )
    return root


def test_launcher_manifest_finds_engines_off_the_search_roots(
    tmp_path: Path, monkeypatch
) -> None:
    """An engine on another drive is invisible to a root-based search.

    Real case: a Windows box keeping every engine on H:\\engines reported zero
    installs, which also stopped engine-bundled templates being filtered out.
    """
    engine = make_engine(tmp_path / "elsewhere" / "UE_5.7", 5, 7, 1)

    manifest = tmp_path / "LauncherInstalled.dat"
    manifest.write_text(
        json.dumps({"InstallationList": [{"InstallLocation": str(engine)}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [manifest])
    monkeypatch.setattr(discovery, "_index_search", lambda pattern: None)
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    installs = discovery.find_engine_installs(roots=[])
    assert [(e.root, e.version) for e in installs] == [(engine, "5.7.1")]


def test_source_built_engines_are_still_found_by_search(tmp_path: Path, monkeypatch) -> None:
    """The manifest only knows about launcher installs."""
    engine = make_engine(tmp_path / "SourceBuild", 5, 6)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(discovery, "_index_search", lambda pattern: None)
    monkeypatch.setattr(
        discovery,
        "_walk_search",
        lambda roots, suffix: [engine / "Engine" / "Build" / "Build.version"],
    )
    installs = discovery.find_engine_installs(roots=[tmp_path])
    assert [e.root for e in installs] == [engine]


def test_a_missing_or_corrupt_manifest_is_not_fatal(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "LauncherInstalled.dat"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [bad, tmp_path / "nope.dat"])
    monkeypatch.setattr(discovery, "_index_search", lambda pattern: None)
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])
    assert discovery.find_engine_installs(roots=[]) == []


def test_projects_inside_an_engine_install_are_excluded(tmp_path: Path, monkeypatch) -> None:
    """Engine templates and samples are not the user's projects."""
    engine = make_engine(tmp_path / "UE_5.8", 5, 8)
    template = engine / "Templates" / "TP_Blank"
    template.mkdir(parents=True)
    (template / "TP_Blank.uproject").write_text("{}", encoding="utf-8")

    mine = tmp_path / "work" / "MyGame"
    mine.mkdir(parents=True)
    (mine / "MyGame.uproject").write_text('{"EngineAssociation": "5.8"}', encoding="utf-8")

    monkeypatch.setattr(
        discovery, "_index_search",
        lambda pattern: [template / "TP_Blank.uproject", mine / "MyGame.uproject"],
    )
    projects = discovery.find_projects(
        engine_installs=[discovery.EngineInstall(root=engine, version="5.8.0")]
    )
    assert [p.name for p in projects] == ["MyGame"]


def test_a_path_returned_twice_is_one_project_not_zero(tmp_path: Path, monkeypatch) -> None:
    """The original launcher's dedup toggled entries, deleting duplicates."""
    mine = tmp_path / "MyGame"
    mine.mkdir()
    uproject = mine / "MyGame.uproject"
    uproject.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(discovery, "_index_search", lambda pattern: [uproject, uproject])
    projects = discovery.find_projects(engine_installs=[])
    assert len(projects) == 1


def test_a_project_folder_named_like_an_engine_is_kept(tmp_path: Path, monkeypatch) -> None:
    """Substring-matching 'engine' hid every project under D:/UnrealEngine Projects."""
    mine = tmp_path / "UnrealEngine Projects" / "MyGame"
    mine.mkdir(parents=True)
    uproject = mine / "MyGame.uproject"
    uproject.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(discovery, "_index_search", lambda pattern: [uproject])
    assert [p.name for p in discovery.find_projects(engine_installs=[])] == ["MyGame"]


def test_walk_prunes_system_trees_and_caps_depth(tmp_path: Path) -> None:
    """A whole-drive fallback walk has to finish, or users assume it hung."""
    (tmp_path / "Windows" / "System32").mkdir(parents=True)
    (tmp_path / "Windows" / "System32" / "Decoy.uproject").write_text("{}", encoding="utf-8")

    shallow = tmp_path / "work" / "MyGame"
    shallow.mkdir(parents=True)
    (shallow / "MyGame.uproject").write_text("{}", encoding="utf-8")

    deep = tmp_path.joinpath(*[f"lvl{i}" for i in range(12)])
    deep.mkdir(parents=True)
    (deep / "TooDeep.uproject").write_text("{}", encoding="utf-8")

    found = {p.name for p in discovery._walk_search([tmp_path], ".uproject")}
    assert "MyGame.uproject" in found
    assert "Decoy.uproject" not in found  # pruned system tree
    assert "TooDeep.uproject" not in found  # past the depth cap
