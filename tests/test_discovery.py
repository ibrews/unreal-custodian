"""Tests for engine and project discovery."""

from __future__ import annotations

import json
import os
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
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: None)
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    installs = discovery.find_engine_installs(roots=[])
    assert [(e.root, e.version) for e in installs] == [(engine, "5.7.1")]


def test_source_built_engines_are_still_found_by_search(tmp_path: Path, monkeypatch) -> None:
    """The manifest only knows about launcher installs."""
    engine = make_engine(tmp_path / "SourceBuild", 5, 6)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: None)
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
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: None)
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
        lambda pattern, **kwargs: [template / "TP_Blank.uproject", mine / "MyGame.uproject"],
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

    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: [uproject, uproject])
    projects = discovery.find_projects(engine_installs=[])
    assert len(projects) == 1


def test_a_project_folder_named_like_an_engine_is_kept(tmp_path: Path, monkeypatch) -> None:
    """Substring-matching 'engine' hid every project under D:/UnrealEngine Projects."""
    mine = tmp_path / "UnrealEngine Projects" / "MyGame"
    mine.mkdir(parents=True)
    uproject = mine / "MyGame.uproject"
    uproject.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: [uproject])
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


def test_project_scan_root_override_scopes_the_project_search(
    tmp_path: Path, monkeypatch
) -> None:
    """CUSTODIAN_PROJECT_SCAN_ROOT restricts *project* discovery to one tree --
    used to isolate a demo/screenshot folder from everything else on a real
    machine (real case: a plugin embedded across many real projects that must
    never appear in a public screenshot, even indirectly via its path)."""
    monkeypatch.setenv("CUSTODIAN_PROJECT_SCAN_ROOT", "/some/demo/folder")
    seen = {}

    def fake_index_search(pattern, **kwargs):
        seen[pattern] = kwargs.get("scan_roots")
        return None

    monkeypatch.setattr(discovery, "_index_search", fake_index_search)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    discovery.find_projects(roots=[], engine_installs=[])
    assert seen["*.uproject"] == ["/some/demo/folder"]


def test_engine_discovery_is_never_scoped_by_the_project_scan_root(
    tmp_path: Path, monkeypatch
) -> None:
    """The override is deliberately project-only -- engine installs carry no
    project-specific information worth isolating, and someone scoping a demo
    folder still wants their real engines to show."""
    monkeypatch.setenv("CUSTODIAN_PROJECT_SCAN_ROOT", "/some/demo/folder")
    seen = {}

    def fake_index_search(pattern, **kwargs):
        seen[pattern] = kwargs.get("scan_roots")
        return None

    monkeypatch.setattr(discovery, "_index_search", fake_index_search)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    discovery.find_engine_installs(roots=[])
    assert seen["Build.version"] is None


def test_scan_root_still_scopes_the_walk_fallback(tmp_path: Path, monkeypatch) -> None:
    """Real gap, caught before it shipped: when no index is available (no
    Everything on Windows, Spotlight disabled on a volume), find_projects()
    fell back to _walk_search(roots or default_roots(), ...) -- which ignores
    CUSTODIAN_PROJECT_SCAN_ROOT entirely and walks the whole machine. A demo
    folder scope has to survive the no-index case, not just the indexed one."""
    demo = tmp_path / "demo"
    demo.mkdir()
    inside = demo / "DemoProject" / "DemoProject.uproject"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}", encoding="utf-8")

    elsewhere = tmp_path / "real-work" / "RealClientProject" / "RealClientProject.uproject"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("CUSTODIAN_PROJECT_SCAN_ROOT", str(demo))
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: None)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(
        discovery,
        "_walk_search",
        lambda roots, suffix: [inside, elsewhere],  # a whole-drive walk finds both
    )

    projects = discovery.find_projects(roots=[], engine_installs=[])
    assert [p.name for p in projects] == ["DemoProject"]


def test_scan_root_filters_an_unscoped_index_result_too(tmp_path: Path, monkeypatch) -> None:
    """es.exe's CUSTODIAN_PROJECT_SCAN_ROOT support is unverified (see
    _index_search) -- if it ever returns results outside scan_root, those
    must still be filtered out rather than trusted as already-scoped."""
    demo = tmp_path / "demo"
    demo.mkdir()
    inside = demo / "DemoProject" / "DemoProject.uproject"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}", encoding="utf-8")

    elsewhere = tmp_path / "real-work" / "RealClientProject" / "RealClientProject.uproject"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("CUSTODIAN_PROJECT_SCAN_ROOT", str(demo))
    # Index "succeeds" but (as on real es.exe today) ignores the scope.
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: [inside, elsewhere])
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])

    projects = discovery.find_projects(roots=[], engine_installs=[])
    assert [p.name for p in projects] == ["DemoProject"]


def test_engine_scan_root_is_independent_of_the_project_scan_root(
    tmp_path: Path, monkeypatch
) -> None:
    """Setting CUSTODIAN_PROJECT_SCAN_ROOT alone must not scope engines --
    that's test_engine_discovery_is_never_scoped_by_the_project_scan_root
    above. This is the other direction: CUSTODIAN_ENGINE_SCAN_ROOT must not
    require CUSTODIAN_PROJECT_SCAN_ROOT to also be set."""
    monkeypatch.setenv("CUSTODIAN_ENGINE_SCAN_ROOT", "/some/demo/folder")
    monkeypatch.delenv("CUSTODIAN_PROJECT_SCAN_ROOT", raising=False)
    seen = {}

    def fake_index_search(pattern, **kwargs):
        seen[pattern] = kwargs.get("scan_roots")
        return None

    monkeypatch.setattr(discovery, "_index_search", fake_index_search)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    discovery.find_engine_installs(roots=[])
    assert seen["Build.version"] == ["/some/demo/folder"]


def test_engine_scan_root_skips_the_launcher_manifest(tmp_path: Path, monkeypatch) -> None:
    """The launcher manifest only ever lists real, launcher-installed
    engines -- there's no synthetic entry it could return, so a scoped scan
    has to skip it outright rather than trust it's somehow already scoped.
    Real case this guards against: a launcher-installed engine whose path
    embeds a client/partner name, same class of leak as the search paths."""
    real_engine = make_engine(tmp_path / "real" / "ClientName_UE58", 5, 8)
    monkeypatch.setenv("CUSTODIAN_ENGINE_SCAN_ROOT", str(tmp_path / "demo"))
    monkeypatch.setattr(discovery, "_launcher_engine_roots", lambda: [real_engine])
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: None)
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    installs = discovery.find_engine_installs(roots=[])
    assert installs == []


def test_engine_scan_root_scopes_the_walk_fallback_and_filters_the_index(
    tmp_path: Path, monkeypatch
) -> None:
    """Same defense-in-depth as the project-side test: CUSTODIAN_ENGINE_SCAN_ROOT
    has to survive both the no-index walk fallback AND an index result that
    (like real es.exe today) ignores the requested scope."""
    demo = tmp_path / "demo"
    demo.mkdir()
    safe = make_engine(demo / "UE_5.8", 5, 8)
    sensitive = make_engine(tmp_path / "real" / "InnerspaceClient_UE52", 5, 2)

    monkeypatch.setenv("CUSTODIAN_ENGINE_SCAN_ROOT", str(demo))
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(
        discovery,
        "_index_search",
        lambda pattern, **kwargs: [
            safe / "Engine" / "Build" / "Build.version",
            sensitive / "Engine" / "Build" / "Build.version",
        ],
    )

    installs = discovery.find_engine_installs(roots=[])
    assert [e.root for e in installs] == [safe]


def test_persisted_settings_scope_both_projects_and_engines(tmp_path: Path, monkeypatch) -> None:
    """Unlike the env vars (deliberately independent), a user's own Settings
    ("only search these drives") apply identically on both sides -- the
    mental model is "only look here at all," not project-vs-engine."""
    from custodian import settings as settings_mod

    monkeypatch.delenv("CUSTODIAN_PROJECT_SCAN_ROOT", raising=False)
    monkeypatch.delenv("CUSTODIAN_ENGINE_SCAN_ROOT", raising=False)
    monkeypatch.setattr(
        settings_mod,
        "load_settings",
        lambda: settings_mod.Settings().with_included_roots(["/allowed/one", "/allowed/two"]),
    )

    seen = {}

    def fake_index_search(pattern, **kwargs):
        seen[pattern] = kwargs.get("scan_roots")
        return None

    monkeypatch.setattr(discovery, "_index_search", fake_index_search)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    discovery.find_engine_installs(roots=[])
    discovery.find_projects(roots=[], engine_installs=[])
    assert seen["Build.version"] == ["/allowed/one", "/allowed/two"]
    assert seen["*.uproject"] == ["/allowed/one", "/allowed/two"]


def test_env_var_still_wins_over_persisted_settings(tmp_path: Path, monkeypatch) -> None:
    """The demo/screenshot env var is a narrower, explicit override and
    should not be silently widened by whatever the user's Settings say."""
    from custodian import settings as settings_mod

    monkeypatch.setenv("CUSTODIAN_PROJECT_SCAN_ROOT", "/demo/only")
    monkeypatch.setattr(
        settings_mod,
        "load_settings",
        lambda: settings_mod.Settings().with_included_roots(["/allowed/one", "/allowed/two"]),
    )

    seen = {}

    def fake_index_search(pattern, **kwargs):
        seen[pattern] = kwargs.get("scan_roots")
        return None

    monkeypatch.setattr(discovery, "_index_search", fake_index_search)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])
    monkeypatch.setattr(discovery, "_walk_search", lambda roots, suffix: [])

    discovery.find_projects(roots=[], engine_installs=[])
    assert seen["*.uproject"] == ["/demo/only"]


def test_index_available_uses_a_switch_es_exe_actually_supports(monkeypatch) -> None:
    """No test ever exercised index_available() -- it shipped calling
    -get-total-file-count, which is not a real es.exe switch (confirmed
    against es.exe 1.1.0.37's own -h output: it errors "Unknown switch",
    exit code 6). _run() only returns output on exit 0, so this silently
    reported "no index" on every Windows machine, es.exe installed or not,
    and the GUI's notice never went away no matter what a user did."""
    if os.name != "nt":
        pytest.skip("Windows-only code path")
    seen_cmd = {}

    def fake_run(cmd, **kw):
        seen_cmd["cmd"] = cmd
        return "12668641"

    monkeypatch.setattr(discovery, "_everything_exe", lambda: "C:/es.exe")
    monkeypatch.setattr(discovery, "_run", fake_run)
    assert discovery.index_available() is True
    assert "-get-total-file-count" not in seen_cmd["cmd"]


def test_index_available_is_false_when_es_exe_errors(monkeypatch) -> None:
    """The old broken switch always hit this path -- kept as a test so a
    future switch change can't silently regress back to "always false"."""
    if os.name != "nt":
        pytest.skip("Windows-only code path")
    monkeypatch.setattr(discovery, "_everything_exe", lambda: "C:/es.exe")
    monkeypatch.setattr(discovery, "_run", lambda cmd, **kw: "")
    assert discovery.index_available() is False


def test_a_permission_denied_index_result_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch
) -> None:
    """Real case (Fort, 2026-08-20): turning on es.exe made project discovery
    return *zero* projects instead of speeding it up. Everything indexes the
    Recycle Bin, so a real machine's index includes a deleted-item stub like
    $RECYCLE.BIN/.../$I....uproject -- permission-denied by design. is_file()
    on it raised PermissionError, uncaught, crashing find_projects() outright
    while 448 real projects sat right there in the same result set."""
    mine = tmp_path / "MyGame"
    mine.mkdir()
    real = mine / "MyGame.uproject"
    real.write_text("{}", encoding="utf-8")

    denied = tmp_path / "$RECYCLE.BIN" / "S-1-5-21-x" / "$I3OYT5Z.uproject"
    denied.parent.mkdir(parents=True)

    real_is_file = Path.is_file

    def is_file_denies_recycle_bin(self: Path) -> bool:
        if "$RECYCLE.BIN" in self.parts:
            raise PermissionError(5, "Access is denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file_denies_recycle_bin)
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: [real, denied])

    projects = discovery.find_projects(engine_installs=[])
    assert [p.name for p in projects] == ["MyGame"]


def test_index_result_is_pruned_the_same_as_the_walk_would_prune_it(
    tmp_path: Path, monkeypatch
) -> None:
    """The walk already skips $-prefixed dirs and System Volume Information
    (_walk_search/_PRUNE_DIRS) -- the index path has no walk to prune, so it
    needs the same exclusion applied to whatever it hands back, independent
    of whether the path even happens to be stat-able."""
    real = tmp_path / "MyGame" / "MyGame.uproject"
    real.parent.mkdir(parents=True)
    real.write_text("{}", encoding="utf-8")

    svi = tmp_path / "System Volume Information" / "Decoy.uproject"
    svi.parent.mkdir(parents=True)
    svi.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: [real, svi])
    projects = discovery.find_projects(engine_installs=[])
    assert [p.name for p in projects] == ["MyGame"]


def test_prune_filter_does_not_exclude_real_appdata_projects(tmp_path: Path, monkeypatch) -> None:
    """_PRUNE_DIRS includes AppData/Windows/WinSxS as a *walk-performance*
    optimization (skip descending into a huge, low-value tree) -- reusing it
    to filter index results would exclude anything genuinely under AppData,
    the exact over-exclusion already fixed once for _EXCLUDE_COMPONENTS
    (pytest's own tmp_path lives under AppData\\Local\\Temp on Windows,
    which is what caught this while fixing the crash above)."""
    under_appdata = tmp_path / "AppData" / "Local" / "MyGame" / "MyGame.uproject"
    under_appdata.parent.mkdir(parents=True)
    under_appdata.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: [under_appdata])
    projects = discovery.find_projects(engine_installs=[])
    assert [p.name for p in projects] == ["MyGame"]


def test_multi_root_walk_fallback_covers_every_configured_root(
    tmp_path: Path, monkeypatch
) -> None:
    """Two allowed drives/folders, no index available -- the walk fallback
    has to actually search both, not just the first."""
    from custodian import settings as settings_mod

    root_a = tmp_path / "DriveA"
    root_b = tmp_path / "DriveB"
    proj_a = root_a / "ProjA" / "ProjA.uproject"
    proj_b = root_b / "ProjB" / "ProjB.uproject"
    for p in (proj_a, proj_b):
        p.parent.mkdir(parents=True)
        p.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("CUSTODIAN_PROJECT_SCAN_ROOT", raising=False)
    monkeypatch.setattr(
        settings_mod,
        "load_settings",
        lambda: settings_mod.Settings().with_included_roots([str(root_a), str(root_b)]),
    )
    monkeypatch.setattr(discovery, "_index_search", lambda pattern, **kwargs: None)
    monkeypatch.setattr(discovery, "_launcher_manifests", lambda: [])

    seen_roots = []

    def fake_walk(roots, suffix):
        seen_roots.extend(roots)
        return [proj_a, proj_b]

    monkeypatch.setattr(discovery, "_walk_search", fake_walk)

    projects = discovery.find_projects(roots=[], engine_installs=[])
    assert set(seen_roots) == {root_a, root_b}
    assert {p.name for p in projects} == {"ProjA", "ProjB"}
