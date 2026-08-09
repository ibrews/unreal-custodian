"""Tests for the live-editor guard.

The guard's first implementation matched the substring "unreal" anywhere in a
command line, which meant the tool's own shell invocation -- run from a
directory named unreal-custodian, with the project path in argv --
looked exactly like a running editor. It refused to clean a project that no
editor had open. These tests pin the distinction.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodian import safedelete  # noqa: E402

EDITOR = (
    "/Users/Shared/Epic Games/UE_5.8/Engine/Binaries/Mac/UnrealEditor.app/"
    "Contents/MacOS/UnrealEditor /Users/Shared/GH/OtherProject/OtherProject.uproject"
)


def test_editor_open_on_this_project_blocks(tmp_path: Path) -> None:
    project = tmp_path / "MyProject"
    project.mkdir()
    cmdline = f"/Engine/Binaries/Mac/UnrealEditor {project}/MyProject.uproject"
    assert safedelete.editor_is_running(project, processes=[cmdline]) is True


def test_editor_open_on_a_different_project_does_not_block(tmp_path: Path) -> None:
    project = tmp_path / "MyProject"
    project.mkdir()
    assert safedelete.editor_is_running(project, processes=[EDITOR]) is False


def test_no_editor_running_does_not_block(tmp_path: Path) -> None:
    project = tmp_path / "MyProject"
    project.mkdir()
    assert safedelete.editor_is_running(project, processes=[]) is False


def test_unreadable_process_list_blocks(tmp_path: Path) -> None:
    """Unknown must fail closed, not open."""
    project = tmp_path / "MyProject"
    project.mkdir()
    assert safedelete.editor_is_running(project, processes=None) is True


def test_helper_processes_are_not_editors() -> None:
    """The long-lived Unreal helpers hold no project open."""
    for helper in (
        "UnrealEditorServices",
        "UnrealTraceServer",
        "zenserver",
        "crashpad_handler",
        "python",
    ):
        assert not safedelete._EDITOR_EXE.match(helper), helper


def test_real_editor_binaries_are_recognized() -> None:
    for exe in (
        "UnrealEditor",
        "UnrealEditor.exe",
        "UnrealEditor-Cmd",
        "UnrealEditor-Cmd.exe",
        "UE4Editor",
        "UnrealEditor-Mac-DebugGame",
    ):
        assert safedelete._EDITOR_EXE.match(exe), exe


def test_trash_is_the_default_and_is_recoverable(tmp_path: Path, monkeypatch) -> None:
    """Nothing is destroyed unless permanent deletion is explicitly requested."""
    target = tmp_path / "Intermediate"
    target.mkdir()
    (target / "artifact.bin").write_bytes(b"x")

    trash = tmp_path / "Trash"
    trash.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(safedelete.sys, "platform", "darwin")

    safedelete.reclaim(target, dry_run=False)
    assert not target.exists()
    assert (tmp_path / ".Trash" / "Intermediate" / "artifact.bin").exists()


def test_dry_run_never_touches_anything(tmp_path: Path) -> None:
    target = tmp_path / "Intermediate"
    target.mkdir()
    safedelete.reclaim(target, dry_run=True, permanent=True)
    assert target.exists()


def test_permanent_deletion_leaves_nothing_behind(tmp_path: Path) -> None:
    target = tmp_path / "Intermediate"
    (target / "nested").mkdir(parents=True)
    (target / "nested" / "artifact.bin").write_bytes(b"x")
    safedelete.reclaim(target, dry_run=False, permanent=True)
    assert not target.exists()


def test_same_named_folders_from_different_projects_do_not_collide(tmp_path: Path) -> None:
    """Real failure, 2026-08-09: cleaning several projects in one run, each with
    a directory literally named "DerivedDataCache" (the common case -- Unreal
    projects all reuse the same folder names), landed two of them on the same
    second-resolution timestamp and the second `move` failed outright with
    "already exists". Both must now land, disambiguated by project name.
    """
    trash = tmp_path / "Trash"
    trash.mkdir()

    project_a = tmp_path / "ProjectA" / "DerivedDataCache"
    project_a.mkdir(parents=True)
    project_b = tmp_path / "ProjectB" / "DerivedDataCache"
    project_b.mkdir(parents=True)

    dest_a = safedelete._move_into_trash(project_a, trash)
    dest_b = safedelete._move_into_trash(project_b, trash)

    assert dest_a != dest_b
    assert dest_a.exists() and dest_b.exists()
    assert not project_a.exists() and not project_b.exists()


def test_three_way_collision_falls_back_to_a_counter(tmp_path: Path) -> None:
    """Even the project-qualified name can collide -- e.g. cleaning the same
    project twice, or two projects that happen to share a parent directory
    name. The counter fallback must still find a free slot rather than fail.
    """
    trash = tmp_path / "Trash"
    trash.mkdir()
    (trash / "DerivedDataCache").mkdir()
    (trash / "Same-DerivedDataCache").mkdir()

    project = tmp_path / "Same" / "DerivedDataCache"
    project.mkdir(parents=True)

    dest = safedelete._move_into_trash(project, trash)
    assert dest == trash / "Same-DerivedDataCache-2"
    assert dest.exists()


def test_trash_name_candidates_never_repeats(tmp_path: Path) -> None:
    """The bug this replaces: a single retry that can itself collide. Assert
    the sequence keeps producing genuinely new names past the first several.
    """
    project = tmp_path / "ProjectA" / "Intermediate"
    seen = set()
    gen = safedelete._trash_name_candidates(tmp_path / "Trash", project)
    for _ in range(20):
        candidate = next(gen)
        assert candidate not in seen
        seen.add(candidate)
