"""Measure how much a project's reclaimable directories are actually holding.

Directory sizing is the expensive operation in this tool -- it is the reason
the GUI must not do its work on the main thread. Everything here is a pure
function so the caller is free to run it in a worker.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import freshness as freshness_mod
from . import policy as policy_mod
from .discovery import EngineInstall, Project


@dataclass(frozen=True)
class TargetSize:
    target: policy_mod.Target
    path: Path
    bytes: int


@dataclass
class ProjectReport:
    project: Project
    policy: policy_mod.Policy
    freshness: freshness_mod.Freshness
    sizes: list[TargetSize] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    error: str | None = None

    @property
    def reclaimable_bytes(self) -> int:
        return sum(s.bytes for s in self.sizes)

    @property
    def age_days(self) -> float | None:
        return self.freshness.age_days

    @property
    def age_eligible(self) -> bool:
        age = self.age_days
        return age is not None and age >= self.policy.min_age_days


@dataclass
class EngineReport:
    engine: EngineInstall
    sizes: list[TargetSize] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(s.bytes for s in self.sizes)

    @property
    def locked_bytes(self) -> int:
        """Space that exists but is not safe to reclaim on this engine kind."""
        return sum(directory_size(p) for p, _ in self.skipped)


def scan_engine(
    engine: EngineInstall, enabled_keys=None
) -> EngineReport:
    resolution = policy_mod.resolve_engine_targets(
        engine.root, engine.installed_build, enabled_keys
    )
    sizes = [
        TargetSize(target=target, path=path, bytes=directory_size(path))
        for target, path in resolution.targets
    ]
    sizes.sort(key=lambda s: s.bytes, reverse=True)
    return EngineReport(engine=engine, sizes=sizes, skipped=resolution.skipped)


def directory_size(path: Path) -> int:
    """Bytes on disk, counting each inode once and ignoring unreadable entries."""
    total = 0
    seen: set[tuple[int, int]] = set()
    for dirpath, dirnames, filenames in os.walk(path, onerror=lambda _: None):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            # Hardlinked files would otherwise be counted once per link.
            if st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            total += st.st_size
    return total


def free_bytes(path: Path) -> int:
    """Free space on the volume holding `path`."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def scan_project(
    project: Project, base_policy: policy_mod.Policy = policy_mod.DEFAULT_POLICY
) -> ProjectReport:
    """Everything the reporter and the cleaner both need, computed once."""
    try:
        policy = policy_mod.load_policy(project.root, base_policy)
    except ValueError as exc:
        policy = base_policy
        fresh = freshness_mod.Freshness(None, None)
        return ProjectReport(project, policy, fresh, error=str(exc))

    fresh = freshness_mod.measure(project.root)
    resolution = policy_mod.resolve_targets(
        project.root, policy, project.is_cpp, fresh.age_days
    )

    sizes = [
        TargetSize(target=target, path=path, bytes=directory_size(path))
        for target, path in resolution.targets
    ]
    sizes.sort(key=lambda s: s.bytes, reverse=True)
    return ProjectReport(
        project, policy, fresh, sizes=sizes, skipped=resolution.skipped
    )


def human(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(value) < 1024.0 or unit == "T":
            return f"{value:,.0f}{unit}" if unit in ("B", "K") else f"{value:,.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}T"
