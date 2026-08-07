"""What is safe to delete, and what is never safe to delete.

This module is the safety boundary of the whole tool. Everything destructive
routes through `resolve_targets`, and `NEVER_DELETE` is asserted at call time
rather than merely documented -- an intention that isn't checked is not a
safety property.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIG_FILENAME = ".ueclean.json"


@dataclass(frozen=True)
class Target:
    """One reclaimable location inside a project."""

    key: str
    relpath: str  # relative to project root; may contain a single '*' segment
    description: str
    default_on: bool
    rebuild_cost: str  # what the user pays to get it back
    min_age_days: int = 0  # extra grace period beyond the project-level one


# Ordered roughly by how much they typically reclaim, largest first.
TARGETS: tuple[Target, ...] = (
    Target(
        key="intermediate",
        relpath="Intermediate",
        description="Build intermediates and compiled shaders",
        default_on=True,
        rebuild_cost="rebuild + shader recompile (minutes to tens of minutes)",
    ),
    Target(
        key="plugin_intermediate",
        relpath="Plugins/*/Intermediate",
        description="Per-plugin build intermediates",
        default_on=True,
        rebuild_cost="rebuilt with the project",
    ),
    Target(
        key="binaries",
        relpath="Binaries",
        description="Compiled editor and game binaries",
        default_on=True,
        rebuild_cost="Blueprint-only: regenerated on open. C++: full rebuild",
    ),
    Target(
        key="plugin_binaries",
        relpath="Plugins/*/Binaries",
        description="Per-plugin compiled binaries",
        default_on=True,
        rebuild_cost="rebuilt with the project",
    ),
    Target(
        key="build",
        relpath="Build",
        description="Platform staging and build receipts",
        default_on=True,
        rebuild_cost="regenerated on next package",
    ),
    Target(
        key="ddc",
        relpath="DerivedDataCache",
        description="Project-local derived data cache",
        default_on=True,
        rebuild_cost="re-derived on open (slow first load)",
    ),
    Target(
        key="cooked",
        relpath="Saved/Cooked",
        description="Cooked content from previous packages",
        default_on=True,
        rebuild_cost="re-cooked on next package",
    ),
    Target(
        key="staged",
        relpath="Saved/StagedBuilds",
        description="Staged packaged builds",
        default_on=True,
        rebuild_cost="re-staged on next package",
    ),
    Target(
        key="logs",
        relpath="Saved/Logs",
        description="Editor and game logs",
        default_on=True,
        rebuild_cost="none (but see freshness -- logs date your last real session)",
    ),
    Target(
        key="crashes",
        relpath="Saved/Crashes",
        description="Crash reports",
        default_on=True,
        rebuild_cost="none",
    ),
    Target(
        key="autosaves",
        relpath="Saved/Autosaves",
        description="Editor autosaves",
        default_on=True,
        # After an editor crash this is sometimes the only copy of an hour's
        # work. Age-gated well beyond the project-level threshold.
        min_age_days=90,
        rebuild_cost="UNRECOVERABLE if it held post-crash work",
    ),
    Target(
        key="saved_config",
        relpath="Saved/Config",
        description="Per-user editor layout and settings",
        default_on=False,
        rebuild_cost="regenerated, but you lose window layout and editor prefs",
    ),
)

TARGETS_BY_KEY = {t.key: t for t in TARGETS}

# Deleting any of these is a bug, not a policy choice. Checked, not just documented.
NEVER_DELETE: frozenset[str] = frozenset(
    {
        "Content",
        "Source",
        "Config",
        "Plugins",  # the directory itself; only its Intermediate/Binaries go
        "Saved/SaveGames",  # real user data
        "Saved",  # only named subdirectories of Saved are ever removed
        "",  # the project root
    }
)


@dataclass(frozen=True)
class Policy:
    """Resolved settings for one project."""

    enabled: bool = True
    min_age_days: int = 14
    # Only reclaim when the volume is below this much free space. Age ranks
    # candidates; pressure decides whether to act at all. Set to 0 to make the
    # age threshold the sole trigger.
    min_free_gb: float = 100.0
    keep_binaries_for_cpp: bool = True
    targets: frozenset[str] = frozenset(t.key for t in TARGETS if t.default_on)

    def with_overrides(self, data: dict) -> "Policy":
        fields = {}
        for name in ("enabled", "min_age_days", "min_free_gb", "keep_binaries_for_cpp"):
            if name in data:
                fields[name] = data[name]
        if "targets" in data:
            unknown = set(data["targets"]) - set(TARGETS_BY_KEY)
            if unknown:
                raise ValueError(f"unknown target(s) in {CONFIG_FILENAME}: {sorted(unknown)}")
            fields["targets"] = frozenset(data["targets"])
        return replace(self, **fields)


DEFAULT_POLICY = Policy()


def load_policy(project_root: Path, base: Policy = DEFAULT_POLICY) -> Policy:
    """Layer a project's `.ueclean.json` over the global defaults."""
    config = project_root / CONFIG_FILENAME
    if not config.is_file():
        return base
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read {config}: {exc}") from exc
    return base.with_overrides(data)


@dataclass
class Resolution:
    """The outcome of applying a policy to one project."""

    targets: list[tuple[Target, Path]] = field(default_factory=list)
    # Paths a safety rule declined to touch, with the reason. These are normal
    # -- a refusal is the guard doing its job -- so they are reported, not raised.
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def _assert_deletable(project_root: Path, path: Path) -> None:
    """Refuse to hand back anything on the never-delete list.

    Resolves symlinks on both sides before comparing. A plugin directory that
    is a symlink into a *different* checkout is common in Unreal projects, and
    deleting through it would reclaim space from a repository the user never
    named.
    """
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        # The full destination goes in --detail; keeping it out of the summary
        # note stops one symlinked plugin from blowing out the table width.
        raise ValueError("symlink resolves outside the project") from exc

    rel_posix = rel.as_posix()
    if rel_posix in NEVER_DELETE or rel_posix == ".":
        raise ValueError(f"protected path: {rel_posix}")
    # Guard the SaveGames tree at any depth.
    if "SaveGames" in rel.parts:
        raise ValueError(f"save data: {rel_posix}")


def _not_writable_reason(path: Path) -> str | None:
    """Explain, once and up front, why this directory cannot be removed.

    Projects copied between machines or created under a different account are
    routinely owned by another uid. Discovering that one failed `move` at a
    time, after the plan has been printed and partially executed, is a much
    worse experience than declining with the reason attached.
    """
    # Removing a directory requires write permission on its parent (to unlink
    # the entry) and on the directory itself (to empty it).
    for probe in (path.parent, path):
        if not os.access(probe, os.W_OK | os.X_OK):
            try:
                owner = probe.stat().st_uid
            except OSError:
                return "not writable by this user"
            if owner != os.getuid():
                return f"owned by uid {owner}, not writable by you"
            return "not writable by this user"
    return None


def resolve_targets(
    project_root: Path, policy: Policy, is_cpp: bool, age_days: float | None
) -> Resolution:
    """Existing paths this policy permits removing, with every safety rule applied.

    A path that fails a safety check is skipped and recorded; it never aborts
    the rest of the project.
    """
    resolution = Resolution()

    def consider(target: Target, candidate: Path) -> None:
        if not candidate.is_dir():
            return
        try:
            _assert_deletable(project_root, candidate)
        except ValueError as exc:
            resolution.skipped.append((candidate, str(exc)))
            return
        unwritable = _not_writable_reason(candidate)
        if unwritable:
            resolution.skipped.append((candidate, unwritable))
            return
        resolution.targets.append((target, candidate))

    for key in sorted(policy.targets):
        target = TARGETS_BY_KEY[key]

        if target.key in ("binaries", "plugin_binaries") and is_cpp and policy.keep_binaries_for_cpp:
            continue
        if target.min_age_days and (age_days is None or age_days < target.min_age_days):
            continue

        if "*" in target.relpath:
            head, _, tail = target.relpath.partition("/*/")
            for child in sorted((project_root / head).glob("*")):
                consider(target, child / tail)
        else:
            consider(target, project_root / target.relpath)

    return resolution
