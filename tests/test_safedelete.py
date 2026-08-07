"""Tests for the live-editor guard.

The guard's first implementation matched the substring "unreal" anywhere in a
command line, which meant the tool's own shell invocation -- run from a
directory named unreal-project-janitor, with the project path in argv --
looked exactly like a running editor. It refused to clean a project that no
editor had open. These tests pin the distinction.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upj import safedelete  # noqa: E402

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
