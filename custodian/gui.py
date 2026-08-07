"""Desktop UI for reviewing and reclaiming Unreal project caches.

Layout follows Marshall's (@nocxr) Unreal Project Launcher -- projects above,
engine installs below, double-click to launch -- with the custodian's size,
freshness and policy columns added.

One structural difference matters: the original scanned on the Tk main thread,
which was fine when the work was an instant Everything query. Measuring
directory sizes for a hundred projects is not instant, so scanning happens on
a worker and results are streamed into the table as they arrive. Doing it the
original way locks the window for minutes.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import policy as policy_mod
from . import safedelete
from .discovery import EngineInstall, Project, find_engine_installs, find_projects
from .sizing import ProjectReport, free_bytes, human, scan_project

GB = 1024**3


class CustodianApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Unreal Custodian")
        root.geometry("1180x760")
        root.minsize(900, 560)

        self.reports: dict[str, ProjectReport] = {}
        self.results: queue.Queue = queue.Queue()
        self.scanning = False

        self.permanent = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready.")
        self.summary = tk.StringVar(value="")

        self._build_layout()
        self.root.after(80, self._drain)
        self.rescan()

    # ---------------------------------------------------------------- layout

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=3)
        outer.rowconfigure(4, weight=1)

        ttk.Label(outer, text="Unreal Projects", font=("", 13, "bold")).grid(
            row=0, column=0, sticky=tk.W, pady=(0, 4)
        )
        self._build_projects(outer)

        ttk.Separator(outer, orient="horizontal").grid(
            row=2, column=0, sticky=tk.EW, pady=8
        )
        ttk.Label(outer, text="Engine Installations", font=("", 13, "bold")).grid(
            row=3, column=0, sticky=tk.W, pady=(0, 4)
        )
        self._build_engines(outer)

        bar = ttk.Frame(outer)
        bar.grid(row=5, column=0, sticky=tk.EW, pady=(8, 0))
        ttk.Label(bar, textvariable=self.status).pack(side=tk.LEFT)
        ttk.Label(bar, textvariable=self.summary, font=("", 11, "bold")).pack(
            side=tk.RIGHT
        )

    def _build_projects(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=1, column=0, sticky=tk.NSEW)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("reclaim", "age", "kind", "engine", "status", "path")
        self.tree = ttk.Treeview(frame, columns=columns, show="tree headings")
        headings = {
            "#0": ("Project", 210),
            "reclaim": ("Reclaimable", 100),
            "age": ("Last touched", 100),
            "kind": ("Type", 60),
            "engine": ("Engine", 70),
            "status": ("Status", 210),
            "path": ("Location", 330),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label, command=lambda k=key: self._sort(k))
            anchor = tk.E if key in ("reclaim", "age") else tk.W
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "path"))

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)

        # Eligibility is the thing a user needs to read at a glance.
        self.tree.tag_configure("eligible", foreground="#b3261e")
        self.tree.tag_configure("blocked", foreground="#8a8a8a")
        self.tree.bind("<Double-1>", lambda _e: self.launch_project())

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(6, 0))
        ttk.Button(buttons, text="Rescan", command=self.rescan).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Launch Project", command=self.launch_project).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(buttons, text="Reveal", command=self.reveal_project).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(
            buttons, text="Never clean this", command=self.toggle_opt_out
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Checkbutton(
            buttons,
            text="Delete permanently instead of to Trash",
            variable=self.permanent,
            command=self._warn_permanent,
        ).pack(side=tk.RIGHT)
        self.clean_button = ttk.Button(
            buttons, text="Clean Selected", command=self.clean_selected
        )
        self.clean_button.pack(side=tk.RIGHT, padx=(0, 10))

    def _build_engines(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=4, column=0, sticky=tk.NSEW)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.engines = ttk.Treeview(
            frame, columns=("version", "path"), show="tree headings", height=4
        )
        self.engines.heading("#0", text="Engine")
        self.engines.heading("version", text="Version")
        self.engines.heading("path", text="Location")
        self.engines.column("#0", width=210)
        self.engines.column("version", width=100)
        self.engines.column("path", width=700, stretch=True)

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.engines.yview)
        self.engines.configure(yscrollcommand=vsb.set)
        self.engines.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)

    # ---------------------------------------------------------------- scanning

    def rescan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.reports.clear()
        self.tree.delete(*self.tree.get_children())
        self.engines.delete(*self.engines.get_children())
        self.status.set("Searching...")
        self.summary.set("")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        """Runs off the main thread; every result goes back through the queue."""
        try:
            engines = find_engine_installs()
            self.results.put(("engines", engines))
            projects = find_projects(engine_installs=engines)
            self.results.put(("count", len(projects)))
            for project in projects:
                self.results.put(("project", scan_project(project)))
        except Exception as exc:  # surfaced in the status bar, never silent
            self.results.put(("error", str(exc)))
        finally:
            self.results.put(("done", None))

    def _drain(self) -> None:
        """Pump worker results into the widgets on the main thread."""
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == "engines":
                    self._add_engines(payload)
                elif kind == "count":
                    self.status.set(f"Measuring {payload} projects...")
                elif kind == "project":
                    self._add_project(payload)
                elif kind == "error":
                    self.status.set(f"Error: {payload}")
                elif kind == "done":
                    self.scanning = False
                    self._refresh_summary()
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _add_engines(self, engines: list[EngineInstall]) -> None:
        for engine in engines:
            self.engines.insert(
                "", tk.END, text=engine.root.name,
                values=(engine.version, str(engine.root)),
            )

    def _add_project(self, report: ProjectReport) -> None:
        # Keyed by the .uproject, not the folder: a single directory can hold
        # more than one project, and keying by folder collides on those.
        key = str(report.project.uproject)
        self.reports[key] = report
        self.tree.insert(
            "", tk.END, iid=key, text=report.project.name,
            values=self._row(report), tags=(self._tag(report),),
        )
        self._refresh_summary()

    def _row(self, report: ProjectReport) -> tuple:
        age = report.age_days
        return (
            human(report.reclaimable_bytes),
            f"{age:,.0f}d" if age is not None else "unknown",
            "C++" if report.project.is_cpp else "Blueprint",
            report.project.engine_association,
            self._status_text(report),
            str(report.project.root),
        )

    def _status_text(self, report: ProjectReport) -> str:
        if report.error:
            return report.error
        if not report.policy.enabled:
            return "never clean (.ueclean.json)"
        if report.skipped:
            return f"{report.skipped[0][1]}"
        if not report.age_eligible:
            return f"used recently (< {report.policy.min_age_days}d)"
        if report.reclaimable_bytes == 0:
            return "already clean"
        return "ready to clean"

    def _tag(self, report: ProjectReport) -> str:
        eligible = (
            report.policy.enabled
            and report.age_eligible
            and report.reclaimable_bytes > 0
            and not report.error
        )
        return "eligible" if eligible else "blocked"

    def _refresh_summary(self) -> None:
        total = sum(r.reclaimable_bytes for r in self.reports.values())
        ready = sum(
            r.reclaimable_bytes
            for r in self.reports.values()
            if self._tag(r) == "eligible"
        )
        self.summary.set(
            f"{human(ready)} ready to reclaim  ·  {human(total)} found  ·  "
            f"{human(free_bytes(Path.home()))} free"
        )
        if not self.scanning:
            self.status.set(f"{len(self.reports)} projects.")

    def _sort(self, column: str) -> None:
        def key(iid: str):
            report = self.reports[iid]
            if column == "reclaim":
                return -report.reclaimable_bytes
            if column == "age":
                return -(report.age_days or 0)
            if column == "#0":
                return report.project.name.lower()
            index = self.tree["columns"].index(column)
            return str(self.tree.item(iid, "values")[index]).lower()

        for position, iid in enumerate(sorted(self.tree.get_children(), key=key)):
            self.tree.move(iid, "", position)

    # ---------------------------------------------------------------- actions

    def _selected(self) -> list[ProjectReport]:
        return [self.reports[iid] for iid in self.tree.selection() if iid in self.reports]

    def launch_project(self) -> None:
        for report in self._selected()[:1]:
            path = report.project.uproject
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform.startswith("win"):
                # startfile avoids the shell entirely, so a path containing '&'
                # or a space cannot be reinterpreted as a command.
                import os

                os.startfile(str(path))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.status.set(f"Launched {path.name}")

    def reveal_project(self) -> None:
        for report in self._selected()[:1]:
            path = report.project.uproject
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])

    def toggle_opt_out(self) -> None:
        """Write (or clear) `enabled: false` in the project's .ueclean.json.

        The config sits next to the .uproject, so projects sharing a directory
        share one policy -- toggling either toggles both.
        """
        import json

        for report in self._selected():
            config = report.project.root / policy_mod.CONFIG_FILENAME
            currently_off = not report.policy.enabled
            try:
                if currently_off:
                    config.unlink(missing_ok=True)
                else:
                    config.write_text(json.dumps({"enabled": False}, indent=2) + "\n",
                                      encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Could not write", str(exc))
                return
            key = str(report.project.uproject)
            self.reports[key] = updated = scan_project(report.project)
            self.tree.item(key, values=self._row(updated), tags=(self._tag(updated),))
        self._refresh_summary()

    def _warn_permanent(self) -> None:
        if not self.permanent.get():
            return
        keep = messagebox.askokcancel(
            "Delete permanently?",
            "Reclaimed folders will be deleted outright, with no way to undo.\n\n"
            "Trash is the default because a wrong policy then costs you a "
            "drag-and-drop. The tradeoff is that trashing frees no disk space "
            "on the same volume until you empty the bin.",
            icon=messagebox.WARNING,
        )
        if not keep:
            self.permanent.set(False)

    def clean_selected(self) -> None:
        chosen = [r for r in self._selected() if self._tag(r) == "eligible"]
        if not chosen:
            messagebox.showinfo(
                "Nothing to clean",
                "Select one or more projects marked “ready to clean”.",
            )
            return

        total = sum(r.reclaimable_bytes for r in chosen)
        where = "deleted permanently" if self.permanent.get() else "moved to the Trash"
        lines = "\n".join(
            f"  {r.project.name} — {human(r.reclaimable_bytes)}" for r in chosen[:12]
        )
        if len(chosen) > 12:
            lines += f"\n  ... and {len(chosen) - 12} more"
        if not messagebox.askokcancel(
            "Clean these projects?",
            f"{human(total)} will be {where}:\n\n{lines}\n\n"
            "Authored content, Config and save games are never touched.",
            icon=messagebox.WARNING,
        ):
            return

        reclaimed, failures, skipped = 0, [], []
        for report in chosen:
            if safedelete.editor_is_running(report.project.root):
                skipped.append(report.project.name)
                continue
            for size in report.sizes:
                try:
                    safedelete.reclaim(
                        size.path, dry_run=False, permanent=self.permanent.get()
                    )
                    reclaimed += size.bytes
                except OSError as exc:
                    failures.append(f"{size.path}: {exc}")

        message = f"Reclaimed {human(reclaimed)}."
        if skipped:
            message += "\n\nSkipped (open in Unreal): " + ", ".join(skipped)
        if failures:
            message += "\n\nFailed:\n" + "\n".join(failures[:8])
        messagebox.showinfo("Done", message)
        self.rescan()


def main() -> int:
    root = tk.Tk()
    CustodianApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
