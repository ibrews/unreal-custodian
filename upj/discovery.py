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
_EXCLUDE_COMPONENTS = {"templates", "samples", "appdata"}


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


def _index_search(pattern: str) -> list[Path] | None:
    """Query the OS file index. Returns None when no index is available."""
    if sys.platform == "darwin":
        # Spotlight. Note this silently skips volumes with indexing disabled,
        # which is why an empty result falls through to the walk.
        out = _run(["mdfind", f"kMDItemFSName == '{pattern}'"])
        paths = [Path(line) for line in out.splitlines() if line.strip()]
        return paths or None

    if os.name == "nt":
        es = _everything_exe()
        if not es:
            return None
        # Request only the full path so that names containing spaces cannot be
        # mis-split by the caller.
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
    return EngineInstall(
        root=engine_root,
        version=version,
        installed_build=_is_installed_build(engine_root),
    )


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
    installs: dict[Path, EngineInstall] = {}
    for engine_root in _launcher_engine_roots():
        engine = _engine_from_root(engine_root)
        if engine:
            installs[engine.root] = engine

    # Source builds and manually-installed engines are not in the manifest.
    found = _index_search("Build.version")
    if found is None:
        found = _walk_search(roots or default_roots(), "Build.version")

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


def find_projects(
    roots: list[Path] | None = None,
    engine_installs: list[EngineInstall] | None = None,
) -> list[Project]:
    """Every user-authored .uproject on this machine."""
    if engine_installs is None:
        engine_installs = find_engine_installs(roots)
    engine_roots = [e.root for e in engine_installs]

    found = _index_search("*.uproject")
    if found is None:
        found = _walk_search(roots or default_roots(), ".uproject")

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
