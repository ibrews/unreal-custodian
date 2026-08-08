"""Desktop UI for reviewing and reclaiming Unreal project caches.

Layout follows Marshall's (@nocxr) Unreal Project Launcher -- projects above,
engine installs below, double-click to launch -- with the custodian's size,
freshness and policy columns added.

One structural difference matters: the original scanned on the Tk main thread,
which was fine when the work was an instant Everything query. Measuring
directory sizes for a hundred projects is not instant, so scanning happens on
a worker and results are streamed into the table as they arrive. Doing it the
original way locks the window for minutes.

Engine installs are reclaimable from here too, not just displayed -- on the
machine this shipped against, engines held far more rebuildable output than
every project combined (146.8 GB on one source build vs. 121 GB across 96
projects). A launcher/binary install's Binaries are refused outright, same as
the CLI; a source build's Intermediate/Binaries stay behind an explicit
checkbox, because reclaiming them costs a multi-hour engine rebuild.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import policy as policy_mod
from . import safedelete
from .discovery import find_engine_installs, find_projects
from .sizing import EngineReport, ProjectReport, free_bytes, human, scan_engine, scan_project


class CustodianApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Unreal Custodian")
        root.geometry("1180x780")
        root.minsize(900, 580)

        self.reports: dict[str, ProjectReport] = {}
        self.engine_reports: dict[str, EngineReport] = {}
        self.results: queue.Queue = queue.Queue()
        self.scanning = False

        self.permanent = tk.BooleanVar(value=False)
        self.engine_rebuildable = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready.")
        self.summary = tk.StringVar(value="")

        # Which project-level folders (Intermediate, Saved/Cooked, DDC, ...)
        # get cleaned. A project's own .ueclean.json still overrides this per
        # project -- this is just the GUI's starting policy, same role as the
        # CLI's built-in defaults.
        self.target_vars: dict[str, tk.BooleanVar] = {
            t.key: tk.BooleanVar(value=t.default_on) for t in policy_mod.TARGETS
        }
        self.targets_summary = tk.StringVar(value="")
        self._refresh_targets_summary()

        self._build_layout()
        self.root.after(80, self._drain)
        self.rescan()

    # ---------------------------------------------------------------- layout

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        # A PanedWindow rather than fixed grid rows: with as few as two engine
        # installs, a fixed-height allocation for that section is mostly empty
        # background. The sash lets a user reclaim that space by hand instead
        # of the tool guessing a split that's wrong for their machine.
        paned = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        top = ttk.Frame(paned)
        bottom = ttk.Frame(paned)
        paned.add(top, weight=3)
        paned.add(bottom, weight=1)
        # Give the projects pane most of the space by default; the sash is
        # still fully draggable afterward.
        self.root.after(60, lambda: self._set_initial_sash(paned))

        ttk.Label(top, text="Unreal Projects", font=("", 13, "bold")).pack(
            side=tk.TOP, anchor=tk.W, pady=(0, 4)
        )
        self._build_projects(top)

        ttk.Label(bottom, text="Engine Installations", font=("", 13, "bold")).pack(
            side=tk.TOP, anchor=tk.W, pady=(0, 4)
        )
        self._build_engines(bottom)

        bar = ttk.Frame(outer)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        ttk.Label(bar, textvariable=self.status).pack(side=tk.LEFT)
        ttk.Label(bar, textvariable=self.summary, font=("", 11, "bold")).pack(
            side=tk.RIGHT
        )

    def _set_initial_sash(self, paned: ttk.PanedWindow) -> None:
        total = paned.winfo_height()
        if total > 100:
            paned.sashpos(0, int(total * 0.72))

    def _build_projects(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
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
            self.tree.heading(key, text=label, command=lambda k=key: self._sort(self.tree, k, self.reports))
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
        # Selecting in one tree clears the other -- "Clean Selected" always
        # acts on an unambiguous set rather than silently unioning both.
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._clear_selection(self.engines))

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
        self.targets_button = ttk.Button(
            buttons, textvariable=self.targets_summary, command=self.open_target_config
        )
        self.targets_button.pack(side=tk.LEFT, padx=(8, 0))

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
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("reclaim", "kind", "status", "path")
        self.engines = ttk.Treeview(frame, columns=columns, show="tree headings", height=5)
        headings = {
            "#0": ("Engine", 150),
            "reclaim": ("Reclaimable", 100),
            "kind": ("Install", 80),
            "status": ("Status", 260),
            "path": ("Location", 460),
        }
        for key, (label, width) in headings.items():
            self.engines.heading(
                key, text=label,
                command=lambda k=key: self._sort(self.engines, k, self.engine_reports),
            )
            self.engines.column(
                key, width=width, anchor=(tk.E if key == "reclaim" else tk.W),
                stretch=(key == "path"),
            )

        self.engines.tag_configure("eligible", foreground="#b3261e")
        self.engines.tag_configure("blocked", foreground="#8a8a8a")
        self.engines.bind(
            "<<TreeviewSelect>>", lambda _e: self._clear_selection(self.tree)
        )

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.engines.yview)
        self.engines.configure(yscrollcommand=vsb.set)
        self.engines.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)

        note = ttk.Checkbutton(
            frame,
            text="Include Intermediate/Binaries on source-built engines "
                 "(reclaims tens of GB, but costs a full engine rebuild)",
            variable=self.engine_rebuildable,
            command=self.rescan,
        )
        note.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

    # ---------------------------------------------------------------- scanning

    def _clear_selection(self, tree: ttk.Treeview) -> None:
        if tree.selection():
            tree.selection_remove(*tree.selection())

    def rescan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.reports.clear()
        self.engine_reports.clear()
        self.tree.delete(*self.tree.get_children())
        self.engines.delete(*self.engines.get_children())
        self.status.set("Searching...")
        self.summary.set("")
        include_rebuildable = self.engine_rebuildable.get()
        base_policy = policy_mod.DEFAULT_POLICY.with_overrides(
            {"targets": [key for key, var in self.target_vars.items() if var.get()]}
        )
        threading.Thread(
            target=self._scan_worker, args=(include_rebuildable, base_policy), daemon=True
        ).start()

    def _scan_worker(self, include_rebuildable: bool, base_policy: policy_mod.Policy) -> None:
        """Runs off the main thread; every result goes back through the queue."""
        try:
            engines = find_engine_installs()
            keys = (
                frozenset(t.key for t in policy_mod.ENGINE_TARGETS)
                if include_rebuildable
                else None
            )
            for engine in engines:
                self.results.put(("engine", scan_engine(engine, keys)))
            projects = find_projects(engine_installs=engines)
            self.results.put(("count", len(projects)))
            for project in projects:
                # A project's own .ueclean.json still layers over this and
                # wins -- base_policy only supplies what the file doesn't say.
                self.results.put(("project", scan_project(project, base_policy)))
        except Exception as exc:  # surfaced in the status bar, never silent
            self.results.put(("error", str(exc)))
        finally:
            self.results.put(("done", None))

    def _drain(self) -> None:
        """Pump worker results into the widgets on the main thread."""
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == "engine":
                    self._add_engine(payload)
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

    def _add_engine(self, report: EngineReport) -> None:
        key = str(report.engine.root)
        self.engine_reports[key] = report
        self.engines.insert(
            "", tk.END, iid=key, text=report.engine.label,
            values=self._engine_row(report), tags=(self._engine_tag(report),),
        )
        self._refresh_summary()

    def _engine_row(self, report: EngineReport) -> tuple:
        return (
            human(report.reclaimable_bytes),
            report.engine.kind,
            self._engine_status_text(report),
            str(report.engine.root),
        )

    def _engine_status_text(self, report: EngineReport) -> str:
        if report.skipped:
            return report.skipped[0][1]
        if report.reclaimable_bytes == 0:
            return "already clean"
        return "ready to clean"

    def _engine_tag(self, report: EngineReport) -> str:
        return "eligible" if report.reclaimable_bytes > 0 else "blocked"

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
        proj_total = sum(r.reclaimable_bytes for r in self.reports.values())
        proj_ready = sum(
            r.reclaimable_bytes for r in self.reports.values() if self._tag(r) == "eligible"
        )
        eng_total = sum(r.reclaimable_bytes for r in self.engine_reports.values())
        eng_ready = sum(
            r.reclaimable_bytes
            for r in self.engine_reports.values()
            if self._engine_tag(r) == "eligible"
        )
        self.summary.set(
            f"{human(proj_ready + eng_ready)} ready to reclaim  ·  "
            f"{human(proj_total + eng_total)} found  ·  "
            f"{human(free_bytes(Path.home()))} free"
        )
        if not self.scanning:
            self.status.set(
                f"{len(self.reports)} projects, {len(self.engine_reports)} engines."
            )

    def _sort(self, tree: ttk.Treeview, column: str, reports: dict) -> None:
        def key(iid: str):
            report = reports[iid]
            if column == "reclaim":
                return -report.reclaimable_bytes
            if column == "age" and hasattr(report, "age_days"):
                return -(report.age_days or 0)
            if column == "#0":
                return tree.item(iid, "text").lower()
            index = tree["columns"].index(column)
            return str(tree.item(iid, "values")[index]).lower()

        for position, iid in enumerate(sorted(tree.get_children(), key=key)):
            tree.move(iid, "", position)

    # ---------------------------------------------------------------- actions

    def _selected_projects(self) -> list[ProjectReport]:
        return [self.reports[iid] for iid in self.tree.selection() if iid in self.reports]

    def _selected_engines(self) -> list[EngineReport]:
        return [
            self.engine_reports[iid]
            for iid in self.engines.selection()
            if iid in self.engine_reports
        ]

    def launch_project(self) -> None:
        for report in self._selected_projects()[:1]:
            path = report.project.uproject
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform.startswith("win"):
                # startfile avoids the shell entirely, so a path containing '&'
                # or a space cannot be reinterpreted as a command.
                os.startfile(str(path))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.status.set(f"Launched {path.name}")

    def reveal_project(self) -> None:
        for report in self._selected_projects()[:1]:
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
        for report in self._selected_projects():
            config = report.project.root / policy_mod.CONFIG_FILENAME
            currently_off = not report.policy.enabled
            try:
                if currently_off:
                    config.unlink(missing_ok=True)
                else:
                    config.write_text(
                        json.dumps({"enabled": False}, indent=2) + "\n", encoding="utf-8"
                    )
            except OSError as exc:
                messagebox.showerror("Could not write", str(exc))
                return
            key = str(report.project.uproject)
            self.reports[key] = updated = scan_project(report.project)
            self.tree.item(key, values=self._row(updated), tags=(self._tag(updated),))
        self._refresh_summary()

    def _refresh_targets_summary(self) -> None:
        n = sum(1 for v in self.target_vars.values() if v.get())
        self.targets_summary.set(f"Clean targets ({n}/{len(self.target_vars)})…")

    def open_target_config(self) -> None:
        """Pick which reclaimable folders (Intermediate, Saved/Cooked, DDC, ...)
        are in scope for this session's cleanup.

        This is the GUI equivalent of a project's `.ueclean.json` `targets`
        list -- a project file still wins over this if one exists; this only
        sets what applies when a project has no file of its own.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Cleanup targets")
        dialog.transient(self.root)
        dialog.resizable(False, True)

        ttk.Label(
            dialog,
            text="Which reclaimable folders should be in scope for projects "
                 "that don't have their own .ueclean.json:",
            wraplength=460, justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=14, pady=(14, 8))

        body = ttk.Frame(dialog)
        body.pack(fill=tk.BOTH, expand=True, padx=14)
        for target in policy_mod.TARGETS:
            row = ttk.Frame(body)
            row.pack(fill=tk.X, pady=2)
            ttk.Checkbutton(
                row, text=target.key, variable=self.target_vars[target.key]
            ).pack(side=tk.LEFT)
            ttk.Label(
                row, text=f"{target.description}  ·  costs: {target.rebuild_cost}",
                foreground="#666666",
            ).pack(side=tk.LEFT, padx=(8, 0))

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=14, pady=12)
        ttk.Button(
            buttons, text="Defaults",
            command=lambda: [
                self.target_vars[t.key].set(t.default_on) for t in policy_mod.TARGETS
            ],
        ).pack(side=tk.LEFT)
        ttk.Button(
            buttons, text="Apply", command=lambda: self._apply_target_config(dialog)
        ).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

    def _apply_target_config(self, dialog: tk.Toplevel) -> None:
        self._refresh_targets_summary()
        dialog.destroy()
        self.rescan()

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
        chosen_projects = [r for r in self._selected_projects() if self._tag(r) == "eligible"]
        chosen_engines = [r for r in self._selected_engines() if self._engine_tag(r) == "eligible"]
        chosen = chosen_projects + chosen_engines
        if not chosen:
            messagebox.showinfo(
                "Nothing to clean",
                "Select one or more rows marked “ready to clean”, "
                "in either the project list or the engine list.",
            )
            return

        total = sum(r.reclaimable_bytes for r in chosen)
        where = "deleted permanently" if self.permanent.get() else "moved to the Trash"
        names = [
            (r.project.name if isinstance(r, ProjectReport) else r.engine.label, r)
            for r in chosen
        ]
        lines = "\n".join(f"  {name} — {human(r.reclaimable_bytes)}" for name, r in names[:12])
        if len(names) > 12:
            lines += f"\n  ... and {len(names) - 12} more"
        if not messagebox.askokcancel(
            "Clean these?",
            f"{human(total)} will be {where}:\n\n{lines}\n\n"
            "Authored content, Config, save games, and a launcher-installed "
            "engine's own binaries are never touched.",
            icon=messagebox.WARNING,
        ):
            return

        reclaimed, failures, skipped = 0, [], []
        for report in chosen:
            guard_root = (
                report.project.root
                if isinstance(report, ProjectReport)
                else report.engine.root
            )
            label = (
                report.project.name if isinstance(report, ProjectReport) else report.engine.label
            )
            if safedelete.editor_is_running(guard_root):
                skipped.append(label)
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
            message += "\n\nSkipped (Unreal is open there): " + ", ".join(skipped)
        if failures:
            message += "\n\nFailed:\n" + "\n".join(failures[:8])
        messagebox.showinfo("Done", message)
        self.rescan()


def _warn_if_tk_too_old() -> None:
    """Tk 8.5 is known to render a blank window on modern macOS.

    No error, no crash -- the process runs fine and the window simply never
    draws. Found live: this shipped once already believed to be verified,
    because every earlier screenshot attempt had captured a different window
    and reported success. A user hitting a blank window has no way to know
    it is a Tk version problem rather than something they did, so this says
    so up front, in the terminal, before the window even opens.
    """
    version = tk.Tcl().eval("info patchlevel")
    major, minor = (int(p) for p in version.split(".")[:2])
    if (major, minor) < (8, 6):
        print(
            f"Warning: Tk {version} detected. Tk older than 8.6 is known to render "
            "a blank window on modern macOS -- the process runs fine, but nothing "
            "draws. If the window that opens is empty, this is why.\n"
            "Fix: brew install python-tk@3.12 && "
            "/opt/homebrew/opt/python@3.12/bin/python3.12 -m custodian.gui\n",
            file=sys.stderr,
        )


def main() -> int:
    _warn_if_tk_too_old()
    root = tk.Tk()
    CustodianApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
