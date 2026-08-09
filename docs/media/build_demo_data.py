"""Build a synthetic project *and engine* tree for capturing README/social
screenshots, so screenshots never expose real project names, plugin names,
client names, or partner names.

Usage:
    python3 docs/media/build_demo_data.py [target-dir]

Set BOTH CUSTODIAN_PROJECT_SCAN_ROOT and CUSTODIAN_ENGINE_SCAN_ROOT (see
custodian/discovery.py) to this same target-dir before scanning -- that
scopes projects AND engines to purely synthetic data, so a screenshot never
has to trust that a machine's real engine paths happen to be safe to show.
That trust turned out to be misplaced in practice: a real source-build
engine path on one test machine was itself named after the client it was
built for (a launcher-installed one on the same machine named the partner
too), which is exactly the class of leak CUSTODIAN_PROJECT_SCAN_ROOT alone
does not cover -- it only scopes project discovery, by design, because most
users still want their real engines visible for real cleanup runs.

Pure stdlib, cross-platform by design: the original bash version couldn't
run on Windows, and a Windows box is exactly where this is needed most.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# name, is_cpp, intermediate_mb, ddc_mb, binaries_mb, age_days, already_clean
PROJECTS = [
    ("SciFiCorridor", True, 2400, 1800, 900, 62, False),
    ("RacingPrototype", False, 1100, 400, 0, 48, False),
    ("ArchVizWalkthrough", True, 0, 0, 0, 90, True),
    ("MetaHumanShowcase", False, 2800, 1200, 0, 33, False),
    ("VRTrainingSim", True, 650, 180, 420, 71, False),
    ("PuzzleAdventure", False, 140, 60, 0, 22, False),
    ("HorrorEscapeRoom", True, 1900, 700, 380, 54, False),
    ("RoboticsSimulator", False, 0, 0, 0, 120, True),
    ("OpenWorldDemo", True, 4600, 2100, 1300, 85, False),
    ("PlatformerPrototype", False, 95, 30, 0, 19, False),
    ("CityBuilderTest", True, 780, 310, 210, 41, False),
    ("FlightSimShowcase", False, 1600, 500, 0, 3, False),
]

# name, major, minor, patch, installed_build, ddc_mb, crashes_mb
ENGINES = [
    ("UE_5.8", 5, 8, 1, True, 2900, 8),
    ("UE_5.6_source", 5, 6, 1, False, 300, 4),
]

CHUNK = 1024 * 1024
# One shared random block, reused across every write. This is placeholder
# cache data whose only job is to occupy real, non-sparse disk space that
# `du` reports correctly -- it doesn't need fresh entropy per file, and
# generating it once instead of per-megabyte is the difference between
# building the ~26 GB demo tree in seconds versus minutes.
_FILLER = os.urandom(CHUNK)


def mk_file(path: Path, mb: int) -> None:
    if mb <= 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        for _ in range(mb):
            f.write(_FILLER)


def backdate(root: Path, age_days: int) -> None:
    stamp = time.time() - age_days * 86400
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (stamp, stamp))


def build_project(demo: Path, name: str, cpp: bool, inter_mb: int, ddc_mb: int,
                   bin_mb: int, age_days: int, already_clean: bool) -> None:
    root = demo / name
    (root / "Content").mkdir(parents=True, exist_ok=True)
    (root / "Config").mkdir(parents=True, exist_ok=True)
    (root / "Saved" / "Logs").mkdir(parents=True, exist_ok=True)
    (root / f"{name}.uproject").write_text('{"EngineAssociation": "5.8"}', encoding="utf-8")
    (root / ".ueclean.json").write_text(
        '{"enabled": true, "min_age_days": 14}', encoding="utf-8"
    )
    # Freshness reads mtimes under Content/Source/Config -- an empty Content
    # dir (the case for every Blueprint-only project here) gives no signal
    # at all, so age comes back None and the project reads as ineligible
    # regardless of the intended age. Real projects always have real content.
    (root / "Content" / "Level.umap.placeholder").write_text("placeholder", encoding="utf-8")
    (root / "Config" / "DefaultEngine.ini").write_text("placeholder", encoding="utf-8")

    if cpp:
        src = root / "Source" / name
        src.mkdir(parents=True, exist_ok=True)
        (src / f"{name}.cpp").write_text("// placeholder", encoding="utf-8")

    if not already_clean:
        mk_file(root / "Intermediate" / "Build" / "placeholder.o", inter_mb)
        mk_file(root / "DerivedDataCache" / "placeholder.ddc", ddc_mb)
        if bin_mb > 0:
            plat = "Mac" if sys.platform == "darwin" else "Win64"
            mk_file(root / "Binaries" / plat / "placeholder.bin", bin_mb)

    backdate(root, age_days)


def build_engine(demo: Path, name: str, major: int, minor: int, patch: int,
                  installed_build: bool, ddc_mb: int, crashes_mb: int) -> None:
    # Layout has to match discovery.py's own expectations exactly:
    # Engine/Build/Build.version, Engine/Source or Engine/Build/BatchFiles
    # to read as an engine at all (not a packaged game), and
    # InstalledBuild.txt / SourceDistribution.txt to pick the right kind.
    root = demo / "_engines" / name
    build_dir = root / "Engine" / "Build"
    (build_dir / "BatchFiles").mkdir(parents=True, exist_ok=True)
    (root / "Engine" / "Source").mkdir(parents=True, exist_ok=True)
    (build_dir / "Build.version").write_text(
        json.dumps({"MajorVersion": major, "MinorVersion": minor, "PatchVersion": patch}),
        encoding="utf-8",
    )
    marker = "InstalledBuild.txt" if installed_build else "SourceDistribution.txt"
    (build_dir / marker).write_text("", encoding="utf-8")

    mk_file(root / "Engine" / "DerivedDataCache" / "placeholder.ddc", ddc_mb)
    mk_file(root / "Engine" / "Saved" / "Crashes" / "placeholder.dmp", crashes_mb)

    backdate(root, age_days=30)


def main() -> None:
    demo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "CustodianDemo"
    if demo.exists():
        import shutil

        shutil.rmtree(demo)
    demo.mkdir(parents=True)

    for row in PROJECTS:
        build_project(demo, *row)
    for row in ENGINES:
        build_engine(demo, *row)

    print(f"Built {len(PROJECTS)} demo projects and {len(ENGINES)} demo engines under {demo}")


if __name__ == "__main__":
    main()
