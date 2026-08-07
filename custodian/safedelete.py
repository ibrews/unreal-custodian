"""Reversible deletion, and the guard against deleting out from under a live editor.

Nothing here calls `rm -rf`. Reclaimed directories go to the Recycle Bin or
Trash so that a mistaken policy costs the user a drag-and-drop rather than a
rebuild -- or, in the worst case, their work. This tool's entire reputation
dies on one story of "it ate my project", so the default is recoverable even
though it is slower and consumes space until the bin is emptied.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


class DeletionRefused(RuntimeError):
    """Raised when it is not safe to proceed."""


def _trash_macos(path: Path) -> None:
    trash = Path.home() / ".Trash"
    trash.mkdir(exist_ok=True)
    dest = trash / path.name
    if dest.exists():
        stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        dest = trash / f"{path.stem} {stamp}{path.suffix}"
    try:
        shutil.move(str(path), str(dest))
    except OSError as exc:
        # Cross-volume moves into ~/.Trash fail; Finder handles those itself.
        if exc.errno != 18:  # EXDEV
            raise
        script = f'tell application "Finder" to delete POSIX file "{path}"'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)


def _trash_windows(path: Path) -> None:
    # SHFileOperationW with FOF_ALLOWUNDO is the documented route to the
    # Recycle Bin. The path buffer must be double-null terminated.
    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    op = SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=FO_DELETE,
        pFrom=str(path.resolve()) + "\0\0",
        pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
        fAnyOperationsAborted=0,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(f"SHFileOperationW failed with code {result} for {path}")


def _trash_linux(path: Path) -> None:
    trash = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ) / "Trash"
    files, info = trash / "files", trash / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)

    name = path.name
    if (files / name).exists():
        name = f"{path.stem}.{datetime.now():%Y%m%d%H%M%S}{path.suffix}"
    # Write the .trashinfo first so the entry is never orphaned.
    (info / f"{name}.trashinfo").write_text(
        "[Trash Info]\n"
        f"Path={path.resolve()}\n"
        f"DeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}\n",
        encoding="utf-8",
    )
    shutil.move(str(path), str(files / name))


def send_to_trash(path: Path) -> None:
    """Move `path` to the platform's recoverable bin."""
    if not path.exists():
        return
    if sys.platform == "darwin":
        _trash_macos(path)
    elif os.name == "nt":
        _trash_windows(path)
    else:
        _trash_linux(path)


# The editor itself, not its helpers. UnrealEditorServices, UnrealTraceServer,
# zenserver and crashpad_handler all run continuously and hold no project open,
# so matching the bare substring "unreal" produces constant false positives.
_EDITOR_EXE = re.compile(
    r"^(?:UE\d*Editor|UnrealEditor|UnrealGame)"
    r"(?:-Cmd|-(?:Mac|Win64|Linux)-\w+)?(?:\.exe)?$",
    re.IGNORECASE,
)


def _editor_processes() -> list[str] | None:
    """Command lines of running Unreal *editor* processes.

    Returns None when the process list could not be read at all, which the
    caller must treat as "unknown", not "none".
    """
    if os.name == "nt":
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "ForEach-Object { $_.Name + '|' + $_.CommandLine }",
            ],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if proc.returncode != 0:
            return None
        rows = []
        for line in proc.stdout.splitlines():
            name, _, cmdline = line.partition("|")
            rows.append((name.strip(), cmdline))
    else:
        # comm= is argv[0] only, so an executable path containing spaces cannot
        # be mis-split the way a whitespace-tokenized command line would be.
        names = subprocess.run(
            ["ps", "-Ao", "pid=,comm="], capture_output=True, text=True, errors="replace"
        )
        args = subprocess.run(
            ["ps", "-Ao", "pid=,command="], capture_output=True, text=True, errors="replace"
        )
        if names.returncode != 0 or args.returncode != 0:
            return None

        by_pid: dict[str, str] = {}
        for line in args.stdout.splitlines():
            pid, _, cmdline = line.strip().partition(" ")
            by_pid[pid] = cmdline
        rows = []
        for line in names.stdout.splitlines():
            pid, _, comm = line.strip().partition(" ")
            rows.append((comm, by_pid.get(pid, "")))

    return [
        cmdline
        for comm, cmdline in rows
        if _EDITOR_EXE.match(os.path.basename(comm.strip()))
    ]


# Distinct from None, which means "the process list could not be read".
_QUERY_SYSTEM = object()


def editor_is_running(project_root: Path, processes=_QUERY_SYSTEM) -> bool:
    """True if an Unreal editor appears to have this project open.

    Deliberately conservative: when the process list cannot be read, this
    reports True so the caller declines to delete.
    """
    if processes is _QUERY_SYSTEM:
        processes = _editor_processes()
    if processes is None:
        return True

    root = str(project_root.resolve()).lower()
    uproject = f"{project_root.name.lower()}.uproject"
    for cmdline in processes:
        low = cmdline.lower()
        if root in low or uproject in low:
            return True
    return False


def delete_permanently(path: Path) -> None:
    """Remove `path` outright, with no way back.

    Trashing and permanent deletion are not interchangeable: on the same
    volume, moving a directory to the Trash frees no disk space at all until
    the bin is emptied. A user cleaning up because they are out of space needs
    this one -- but it is never the default.
    """
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=False)


def reclaim(path: Path, dry_run: bool = True, permanent: bool = False) -> None:
    """Delete one resolved target. Callers must have passed it through policy first."""
    if dry_run:
        return
    if permanent:
        delete_permanently(path)
    else:
        send_to_trash(path)
