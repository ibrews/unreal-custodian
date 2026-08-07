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

    @property
    def label(self) -> str:
        return f"UE {self.version}"


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


def _walk_search(roots: list[Path], filename_suffix: str) -> list[Path]:
    """Fallback: prune-heavy filesystem walk."""
    hits: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _: None):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
            for fn in filenames:
                if fn.endswith(filename_suffix):
                    hits.append(Path(dirpath) / fn)
    return hits


def default_roots() -> list[Path]:
    """Reasonable places to look when no index is available."""
    home = Path.home()
    candidates = [
        home / "Documents" / "Unreal Projects",
        home / "Documents" / "Unreal Engine",
        Path("/Users/Shared"),
        home / "dev",
        home / "GH",
        Path("D:/") if os.name == "nt" else None,
    ]
    return [c for c in candidates if c is not None and c.is_dir()]


def find_engine_installs(roots: list[Path] | None = None) -> list[EngineInstall]:
    """Locate engine installs by their Build.version manifest."""
    found = _index_search("Build.version")
    if found is None:
        found = _walk_search(roots or default_roots(), "Build.version")

    installs: dict[Path, EngineInstall] = {}
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
        installs[engine_root] = EngineInstall(root=engine_root, version=version)

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
