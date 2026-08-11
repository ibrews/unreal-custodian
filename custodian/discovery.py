"""Locate Unreal projects and engine installs.

Uses the operating system's file index where one is available. A recursive
filesystem walk for `*.uproject` across a developer's drives takes minutes;
Spotlight and Everything answer the same question in well under a second.
The walk is kept only as a fallback for unindexed volumes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import settings as settings_mod

# Shared by the CLI's startup warning and the GUI's dismissible notice --
# one URL, not two copies that can drift apart.
EVERYTHING_DOWNLOAD_URL = "https://www.voidtools.com/downloads/#cli"

# Directory names that never contain a project we care about, and that are
# expensive to descend into during the fallback walk.
_PRUNE_DIRS = {
    "Intermediate",
    "Saved",
    "Binaries",
    "DerivedDataCache",
    "node_modules",
    ".git",
    ".svn",
    # System trees that hold no user projects and are enormous to descend.
    # Without these, a whole-drive fallback walk on Windows takes long enough
    # that users assume the tool has hung.
    "Windows",
    "WinSxS",
    "$Recycle.Bin",
    "System Volume Information",
    "AppData",
    "Library",  # macOS: caches, containers, no .uproject worth finding
}

# Path *components* (not substrings) that mark a project as engine-supplied
# rather than user-authored. Matching components rather than substrings matters:
# a user whose projects live in "D:/UnrealEngine Projects" must not have their
# entire library filtered out because the string "engine" appears in the path.
#
# "appdata" was here too, inherited as a heuristic for skipping launcher caches.
# It is gone: every path under a Windows temp directory contains an AppData
# component, so it silently excluded far more than intended, and engine-root
# exclusion already covers the case it was meant to catch.
_EXCLUDE_COMPONENTS = {"templates", "samples"}


@dataclass(frozen=True)
class EngineInstall:
    root: Path  # the directory *containing* Engine/
    version: str
    # Launcher/binary installs ship precompiled Binaries -- those ARE the
    # engine, not build output, and deleting them means re-downloading tens of
    # gigabytes. A source build can rebuild everything it holds.
    installed_build: bool = True

    @property
    def label(self) -> str:
        return f"UE {self.version}"

    @property
    def kind(self) -> str:
        return "installed" if self.installed_build else "source"


@dataclass(frozen=True)
class Project:
    uproject: Path
    engine_association: str

    @property
    def root(self) -> Path:
        return self.uproject.parent

    @property
    def name(self) -> str:
        return self.uproject.stem

    @property
    def is_cpp(self) -> bool:
        """C++ projects pay a much larger rebuild cost when Binaries are removed."""
        return (self.root / "Source").is_dir()


def _run(cmd: list[str], **kw) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=120, **kw
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _everything_exe() -> str | None:
    """Path to Everything's CLI (es.exe), if the user has it installed."""
    found = shutil.which("es.exe") or shutil.which("es")
    if found:
        return found
    local = Path(__file__).resolve().parent.parent / "es.exe"
    return str(local) if local.exists() else None


def _env_scan_roots(var_name: str) -> list[str] | None:
    """A single explicit root from an env var, wrapped as a one-item list.

    Not a normal end-user setting -- for scoping a scan to one specific
    folder (a demo/screenshot directory, a project tree isolated from
    everything else the index would otherwise return) without touching
    anything else on the machine, and without requiring a settings file to
    exist. CUSTODIAN_PROJECT_SCAN_ROOT and CUSTODIAN_ENGINE_SCAN_ROOT are
    deliberately independent -- see _project_scan_roots/_engine_scan_roots.
    """
    value = os.environ.get(var_name)
    return [value] if value else None


def _project_scan_roots() -> list[str] | None:
    """Roots to restrict *project* discovery to, or None for unrestricted.

    CUSTODIAN_PROJECT_SCAN_ROOT wins if set (see _env_scan_roots). Otherwise
    falls through to the user's own persisted Settings ("only search these
    drives/folders") -- unlike the env vars, one Settings.included_roots
    list scopes both projects and engines identically, because that is the
    mental model a user reaches for this with: "only look here at all," not
    "look here for projects but somewhere else for engines."
    """
    env = _env_scan_roots("CUSTODIAN_PROJECT_SCAN_ROOT")
    if env is not None:
        return env
    resolved = settings_mod.load_settings().resolved_roots()
    return [str(r) for r in resolved] if resolved is not None else None


def _engine_scan_roots() -> list[str] | None:
    """Roots to restrict *engine* discovery to, or None for unrestricted.

    An engine root's own path can carry the same kind of sensitive naming a
    project's can (a source build folder named after the client it was
    built for) -- real case, not hypothetical. CUSTODIAN_ENGINE_SCAN_ROOT
    wins if set; otherwise the same user Settings as _project_scan_roots.
    """
    env = _env_scan_roots("CUSTODIAN_ENGINE_SCAN_ROOT")
    if env is not None:
        return env
    resolved = settings_mod.load_settings().resolved_roots()
    return [str(r) for r in resolved] if resolved is not None else None


def _index_search(pattern: str, *, scan_roots: list[str] | None = None) -> list[Path] | None:
    """Query the OS file index. Returns None when no index is available."""
    if sys.platform == "darwin":
        # Spotlight. Note this silently skips volumes with indexing disabled,
        # which is why an empty result falls through to the walk.
        if scan_roots:
            # mdfind's -onlyin takes one directory, not a list -- restricting
            # to N roots means N calls, merged.
            paths: list[Path] = []
            for root in scan_roots:
                out = _run(["mdfind", "-onlyin", root, f"kMDItemFSName == '{pattern}'"])
                paths.extend(Path(line) for line in out.splitlines() if line.strip())
            return paths or None
        out = _run(["mdfind", f"kMDItemFSName == '{pattern}'"])
        paths = [Path(line) for line in out.splitlines() if line.strip()]
        return paths or None

    if os.name == "nt":
        es = _everything_exe()
        if not es:
            return None
        # Request only the full path so that names containing spaces cannot be
        # mis-split by the caller. scan_roots is not passed to es.exe here --
        # unverified which flag scopes a search to specific folders, and
        # guessing wrong would silently return unscoped results. Every caller
        # filters the result set to scan_roots afterward regardless, so an
        # unscoped result from here is still safe, just slower than an
        # es.exe-native scope would be.
        out = _run([es, pattern, "-full-path-and-name"])
        paths = [Path(line.strip()) for line in out.splitlines() if line.strip()]
        return paths or None

    return None


# Deep enough for any sane layout, shallow enough to finish. Only applies to
# the fallback walk; an indexed search has no depth limit.
_MAX_WALK_DEPTH = 8


def _walk_search(roots: list[Path], filename_suffix: str) -> list[Path]:
    """Fallback: prune-heavy, depth-capped filesystem walk."""
    hits: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _: None):
            if len(Path(dirpath).parts) - base_depth >= _MAX_WALK_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [
                d for d in dirnames if d not in _PRUNE_DIRS and not d.startswith("$")
            ]
            for fn in filenames:
                if fn.endswith(filename_suffix):
                    hits.append(Path(dirpath) / fn)
    return hits


def index_available() -> bool:
    """Is a fast file index usable, or are we about to fall back to walking?"""
    if sys.platform == "darwin":
        return bool(_run(["mdutil", "-s", "/"]))
    if os.name == "nt":
        return _everything_exe() is not None and bool(
            _run([_everything_exe(), "-get-total-file-count"])
        )
    return False


def _fixed_drives() -> list[Path]:
    """Every fixed drive letter on Windows.

    Engines and projects routinely live on a secondary drive -- the machine
    this was tested against keeps all of its engines on H: -- so a default
    root list built only from the user profile finds nothing.
    """
    drives = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:/")
        if root.is_dir():
            drives.append(root)
    return drives


def default_roots() -> list[Path]:
    """Reasonable places to look when no index is available."""
    home = Path.home()
    if os.name == "nt":
        # Slow, but correct. Everything is the fast path; this is the fallback
        # that has to actually find things when it is absent.
        return _fixed_drives()
    candidates = [
        home / "Documents" / "Unreal Projects",
        home / "Documents" / "Unreal Engine",
        Path("/Users/Shared"),
        home / "dev",
        home / "GH",
    ]
    return [c for c in candidates if c.is_dir()]


def _launcher_manifests() -> list[Path]:
    """Where the Epic Games Launcher records what it installed."""
    if os.name == "nt":
        return [Path("C:/ProgramData/Epic/UnrealEngineLauncher/LauncherInstalled.dat")]
    return [
        Path("/Users/Shared/Epic/UnrealEngineLauncher/LauncherInstalled.dat"),
        Path.home()
        / "Library/Application Support/Epic/UnrealEngineLauncher/LauncherInstalled.dat",
    ]


def _launcher_engine_roots() -> list[Path]:
    """Engine locations straight from the launcher's own manifest.

    Authoritative and instant, where searching for Build.version is neither --
    it misses any engine on a drive the search roots do not cover.
    """
    roots: list[Path] = []
    for manifest in _launcher_manifests():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in data.get("InstallationList", []):
            location = entry.get("InstallLocation")
            if location:
                roots.append(Path(location))
    return roots


def _engine_from_root(engine_root: Path) -> EngineInstall | None:
    manifest = engine_root / "Engine" / "Build" / "Build.version"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = ".".join(
        str(data.get(k, 0)) for k in ("MajorVersion", "MinorVersion", "PatchVersion")
    )
    if not _is_engine_install(engine_root):
        return None
    return EngineInstall(
        root=engine_root,
        version=version,
        installed_build=_is_installed_build(engine_root),
    )


def _is_engine_install(engine_root: Path) -> bool:
    """Distinguish an engine install from a packaged game that embeds one.

    A shipped build carries Engine/Build/Build.version and Engine/Content but
    none of the machinery to build with -- so scanning for Build.version alone
    reports every packaged game, and every Fortnite install, as an engine.
    """
    engine = engine_root / "Engine"
    return (engine / "Source").is_dir() or (engine / "Build" / "BatchFiles").is_dir()


def _is_installed_build(engine_root: Path) -> bool:
    """Distinguish a launcher/binary install from a source build.

    UnrealBuildTool drops InstalledBuild.txt into binary installs; source
    distributions get SourceDistribution.txt instead. Getting this wrong in the
    unsafe direction destroys a precompiled engine, so an engine with neither
    marker is treated as installed.
    """
    build = engine_root / "Engine" / "Build"
    if (build / "InstalledBuild.txt").is_file():
        return True
    if (build / "SourceDistribution.txt").is_file():
        return False
    return True


def find_engine_installs(roots: list[Path] | None = None) -> list[EngineInstall]:
    """Locate engine installs, launcher manifest first, then by searching."""
    engine_roots_restriction = _engine_scan_roots()
    installs: dict[Path, EngineInstall] = {}
    if engine_roots_restriction is None:
        # Consulting the manifest directly (rather than only searching for
        # Build.version) matters when unrestricted: it finds an engine on a
        # drive the search roots don't cover. When restricted, skip it --
        # not a loss, because a launcher-installed engine still has its own
        # Build.version on disk, so it's found the same way as any other
        # engine by the search below if it's actually under an allowed root.
        for engine_root in _launcher_engine_roots():
            engine = _engine_from_root(engine_root)
            if engine:
                installs[engine.root] = engine

    # Source builds and manually-installed engines are not in the manifest.
    found = _index_search("Build.version", scan_roots=engine_roots_restriction)
    if found is None:
        walk_roots = (
            [Path(r) for r in engine_roots_restriction]
            if engine_roots_restriction
            else (roots or default_roots())
        )
        found = _walk_search(walk_roots, "Build.version")

    if engine_roots_restriction is not None:
        # Same defense in depth as find_projects(): don't trust that the
        # index actually honored the scope (es.exe's support is unverified).
        bases = [Path(r).resolve() for r in engine_roots_restriction]
        found = [p for p in found if _is_within(p, bases)]

    for manifest in found:
        # .../<EngineRoot>/Engine/Build/Build.version
        try:
            if manifest.parent.name != "Build" or manifest.parent.parent.name != "Engine":
                continue
            engine_root = manifest.parent.parent.parent
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        version = ".".join(
            str(data.get(k, 0)) for k in ("MajorVersion", "MinorVersion", "PatchVersion")
        )
        if not _is_engine_install(engine_root):
            continue
        installs.setdefault(
            engine_root,
            EngineInstall(
                root=engine_root,
                version=version,
                installed_build=_is_installed_build(engine_root),
            ),
        )

    return sorted(installs.values(), key=lambda e: str(e.root))


def _is_excluded(uproject: Path, engine_roots: list[Path]) -> bool:
    """Engine-bundled templates, samples, and anything inside an engine install."""
    parts_lower = {p.lower() for p in uproject.parts}
    if parts_lower & _EXCLUDE_COMPONENTS:
        return True
    for engine_root in engine_roots:
        try:
            uproject.relative_to(engine_root)
            return True
        except ValueError:
            continue
    return False


def _engine_association(uproject: Path) -> str:
    try:
        data = json.loads(uproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    return str(data.get("EngineAssociation") or "not specified")


def _is_within(path: Path, bases: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for base in bases:
        try:
            resolved.relative_to(base)
            return True
        except ValueError:
            continue
    return False


def find_projects(
    roots: list[Path] | None = None,
    engine_installs: list[EngineInstall] | None = None,
) -> list[Project]:
    """Every user-authored .uproject on this machine."""
    if engine_installs is None:
        engine_installs = find_engine_installs(roots)
    engine_roots = [e.root for e in engine_installs]

    roots_restriction = _project_scan_roots()
    found = _index_search("*.uproject", scan_roots=roots_restriction)
    if found is None:
        walk_roots = (
            [Path(r) for r in roots_restriction] if roots_restriction else (roots or default_roots())
        )
        found = _walk_search(walk_roots, ".uproject")

    if roots_restriction is not None:
        # Defense in depth, not just belt-and-suspenders: es.exe's own scoping
        # support is unverified (see _index_search), so a result that reached
        # here from the index path may not actually be scoped at all. A
        # restriction has to be airtight regardless of which discovery path
        # produced `found`, not contingent on every path honoring it.
        bases = [Path(r).resolve() for r in roots_restriction]
        found = [p for p in found if _is_within(p, bases)]

    # Deduplicate by resolved path. A path returned twice by the index is one
    # project, not zero -- last write wins.
    projects: dict[str, Project] = {}
    for uproject in found:
        if not uproject.is_file() or _is_excluded(uproject, engine_roots):
            continue
        key = str(uproject.resolve()).lower()
        projects[key] = Project(
            uproject=uproject, engine_association=_engine_association(uproject)
        )

    return sorted(projects.values(), key=lambda p: p.name.lower())
