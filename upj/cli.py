"""Command line entry point.

    upj report              inventory every project and what it is holding
    upj clean               show exactly what would be reclaimed (default: dry run)
    upj clean --apply       actually reclaim it, to the Trash / Recycle Bin
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import policy as policy_mod
from . import safedelete
from .discovery import find_engine_installs, find_projects
from .sizing import (
    EngineReport,
    ProjectReport,
    free_bytes,
    human,
    scan_engine,
    scan_project,
)

GB = 1024**3


def _scan_all(
    base_policy: policy_mod.Policy, quiet: bool, only: str | None = None
) -> list[ProjectReport]:
    engines = find_engine_installs()
    projects = find_projects(engine_installs=engines)

    if only:
        needle = only.lower()
        projects = [
            p for p in projects if needle in p.name.lower() or needle in str(p.root).lower()
        ]

    if not quiet:
        print(f"Found {len(engines)} engine install(s), {len(projects)} project(s).")
        for engine in engines:
            print(f"  {engine.label:<12} {engine.root}")
        if projects:
            print("Measuring... (directory sizing is the slow part)", flush=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(pool.map(lambda p: scan_project(p, base_policy), projects))
    reports.sort(key=lambda r: r.reclaimable_bytes, reverse=True)
    return reports


def _labels(reports: list[ProjectReport]) -> dict[int, str]:
    """Display names, disambiguated by parent directory when they collide.

    Copied and versioned projects share a name constantly ("StressTest" nine
    times over), and a table that cannot tell them apart is worse than useless
    when the next step is deleting things.
    """
    counts: dict[str, int] = {}
    for report in reports:
        counts[report.project.name] = counts.get(report.project.name, 0) + 1

    labels: dict[int, str] = {}
    for report in reports:
        name = report.project.name
        if counts[name] > 1:
            labels[id(report)] = f"{report.project.root.parent.name}/{name}"
        else:
            labels[id(report)] = name
    return labels


def _print_table(reports: list[ProjectReport]) -> None:
    labels = _labels(reports)
    header = f"{'PROJECT':<38} {'RECLAIM':>9}  {'AGE':>7}  {'KIND':<5} {'NOTES'}"
    print()
    print(header)
    print("-" * len(header))

    for report in reports:
        label = labels[id(report)][:38]
        if report.error:
            print(f"{label:<38} {'-':>9}  {'-':>7}  {'':<5} ERROR: {report.error}")
            continue

        age = report.age_days
        age_text = f"{age:,.0f}d" if age is not None else "-"
        kind = "C++" if report.project.is_cpp else "BP"

        notes = []
        if not report.policy.enabled:
            notes.append("opted out (.ueclean.json)")
        elif not report.age_eligible:
            notes.append(f"too recent (<{report.policy.min_age_days}d)")
        if report.freshness.mtimes_look_rewritten:
            notes.append("mtimes look rewritten by a copy; using log dates")
        if report.project.is_cpp and report.policy.keep_binaries_for_cpp:
            notes.append("Binaries kept (C++ rebuild cost)")
        for path, reason in report.skipped:
            notes.append(f"skipped {path.name} ({reason})")

        print(
            f"{label:<38} {human(report.reclaimable_bytes):>9}  "
            f"{age_text:>7}  {kind:<5} {'; '.join(notes)}"
        )


def _print_detail(reports: list[ProjectReport]) -> None:
    for report in reports:
        if not report.sizes:
            continue
        print(f"\n{report.project.root}")
        for size in report.sizes:
            rel = size.path.relative_to(report.project.root)
            print(
                f"    {human(size.bytes):>9}  {rel.as_posix():<32} "
                f"{size.target.rebuild_cost}"
            )


def _engine_keys(args: argparse.Namespace) -> frozenset[str] | None:
    """Opt in to the expensive engine targets."""
    if not getattr(args, "engine_rebuildable", False):
        return None
    return frozenset(t.key for t in policy_mod.ENGINE_TARGETS)


def _print_engines(reports: list[EngineReport]) -> None:
    if not reports:
        return
    print("\nENGINE INSTALLS")
    print("-" * 70)
    for report in reports:
        engine = report.engine
        print(
            f"  {engine.label} ({engine.kind})  {human(report.reclaimable_bytes):>9} "
            f"reclaimable   {engine.root}"
        )
        for size in report.sizes:
            rel = size.path.relative_to(engine.root).as_posix()
            print(f"      {human(size.bytes):>9}  {rel:<32} {size.target.rebuild_cost}")
        for path, reason in report.skipped:
            rel = path.relative_to(engine.root).as_posix()
            print(f"      {'--':>9}  {rel:<32} SKIPPED: {reason}")


def cmd_report(args: argparse.Namespace) -> int:
    engines = find_engine_installs()
    engine_reports = [scan_engine(e, _engine_keys(args)) for e in engines]

    reports = _scan_all(policy_mod.DEFAULT_POLICY, args.quiet, args.only)
    _print_table(reports)
    if args.detail:
        _print_detail(reports)
    _print_engines(engine_reports)

    total = sum(r.reclaimable_bytes for r in reports) + sum(
        r.reclaimable_bytes for r in engine_reports
    )
    free = free_bytes(Path.home())
    print()
    print(f"TOTAL RECLAIMABLE: {human(total)}")
    print(f"FREE ON THIS VOLUME: {human(free)}")
    print("\nNothing was deleted. `upj clean` shows what a cleanup would remove.")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    base = policy_mod.DEFAULT_POLICY
    if args.min_age_days is not None:
        base = base.with_overrides({"min_age_days": args.min_age_days})
    if args.min_free_gb is not None:
        base = base.with_overrides({"min_free_gb": args.min_free_gb})

    reports = _scan_all(base, args.quiet, args.only)
    free = free_bytes(Path.home())
    threshold = base.min_free_gb * GB

    if threshold and free >= threshold and not args.ignore_pressure:
        print(
            f"\n{human(free)} free, at or above the {base.min_free_gb:g}G threshold. "
            "Nothing to do.\n"
            "Pass --ignore-pressure to reclaim on age alone."
        )
        return 0

    eligible = [
        r
        for r in reports
        if r.policy.enabled and r.age_eligible and r.reclaimable_bytes > 0 and not r.error
    ]
    if not eligible:
        print("\nNo project is currently eligible for cleanup.")
        return 0

    # Largest first: the barbell distribution of Unreal caches means a couple of
    # projects usually account for most of the reclaimable space.
    planned: list[ProjectReport] = []
    projected = free
    for report in eligible:
        if threshold and projected >= threshold and not args.ignore_pressure:
            break
        planned.append(report)
        projected += report.reclaimable_bytes

    verb = "Reclaiming" if args.apply else "Would reclaim"
    destination = (
        "PERMANENTLY (no undo)" if args.permanent else "to the Trash / Recycle Bin"
    )
    print(f"\n{verb} {destination} from {len(planned)} project(s):\n")

    reclaimed = 0
    for report in planned:
        if safedelete.editor_is_running(report.project.root):
            print(f"  SKIP {report.project.name}: an Unreal process has this project open")
            continue

        print(f"  {report.project.name}  ({human(report.reclaimable_bytes)})")
        for size in report.sizes:
            rel = size.path.relative_to(report.project.root).as_posix()
            print(f"      {human(size.bytes):>9}  {rel}")
            try:
                safedelete.reclaim(
                    size.path, dry_run=not args.apply, permanent=args.permanent
                )
            except OSError as exc:
                print(f"      FAILED: {exc}")
                continue
            reclaimed += size.bytes

    print()
    if args.apply and args.permanent:
        print(f"Permanently deleted {human(reclaimed)}. Disk space is freed immediately.")
    elif args.apply:
        print(
            f"Reclaimed {human(reclaimed)} to the Trash / Recycle Bin (recoverable).\n"
            "Note: on the same volume this frees no disk space until the bin is "
            "emptied. Use --permanent to free it immediately."
        )
    else:
        print(f"Dry run. {human(reclaimed)} would be reclaimed. Re-run with --apply.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upj", description="Find and reclaim regeneratable Unreal Engine build caches."
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="inventory projects and reclaimable space")
    report.add_argument("--detail", action="store_true", help="break down by directory")
    report.add_argument("--only", help="limit to projects matching this name or path")
    report.add_argument(
        "--engine-rebuildable",
        action="store_true",
        help="also count engine Intermediate/Binaries (source-built engines only; "
             "reclaims tens of GB but costs a full engine rebuild)",
    )
    report.set_defaults(func=cmd_report)

    clean = sub.add_parser("clean", help="reclaim space (dry run unless --apply)")
    clean.add_argument("--apply", action="store_true", help="actually delete (to Trash)")
    clean.add_argument("--only", help="limit to projects matching this name or path")
    clean.add_argument(
        "--permanent",
        action="store_true",
        help="delete outright instead of to the Trash (frees space immediately, no undo)",
    )
    clean.add_argument("--min-age-days", type=int, help="override the age threshold")
    clean.add_argument("--min-free-gb", type=float, help="override the free-space threshold")
    clean.add_argument(
        "--ignore-pressure",
        action="store_true",
        help="clean every eligible project regardless of free space",
    )
    clean.set_defaults(func=cmd_clean)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
