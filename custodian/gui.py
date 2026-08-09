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
        # Two independent checkboxes, not one: reclaiming Engine/Intermediate
        # never affects whether the editor launches (it's a compile cache).
        # Reclaiming Engine/Binaries does -- the editor will not start again
        # until the engine is rebuilt. Bundling them under one control would
        # let someone reach for the safe half and get the destructive half.
        self.engine_intermediate = tk.BooleanVar(value=False)
        self.engine_binaries = tk.BooleanVar(value=False)
        self.hide_clean = tk.BooleanVar(value=False)
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

        # Clean Selected lives here, not inside either table's own button row:
        # it acts on whichever table has a selection, project or engine, so it
        # belongs to the panel as a whole rather than looking owned by one
        # side. Made deliberately larger/bolder -- it's the one button whose
        # consequence is irreversible-by-default-feeling even though it isn't.
        style = ttk.Style()
        style.configure("Clean.TButton", font=("", 12, "bold"), padding=(18, 10))

        bar = ttk.Frame(outer)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))

        status_row = ttk.Frame(bar)
        status_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(status_row, textvariable=self.status).pack(side=tk.LEFT)
        ttk.Label(status_row, textvariable=self.summary, font=("", 11, "bold")).pack(
            side=tk.RIGHT
        )

        action_row = ttk.Frame(bar)
        action_row.pack(side=tk.TOP, fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(
            action_row,
            text="Delete permanently instead of to Trash",
            variable=self.permanent,
            command=self._warn_permanent,
        ).pack(side=tk.LEFT)
        self.clean_button = ttk.Button(
            action_row, text="Clean Selected", style="Clean.TButton",
            command=self.clean_selected,
        )
        self.clean_button.pack(side=tk.RIGHT)

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
        # Also refreshes the summary line so "ready to reclaim" tracks the
        # live selection instead of every eligible row on the machine.
        self.tree.bind(
            "<<TreeviewSelect>>",
            lambda _e: (self._clear_selection(self.engines), self._refresh_summary()),
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(6, 0))
        ttk.Button(buttons, text="Rescan", command=self.rescan).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Select All", command=self.select_all_projects).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(buttons, text="Select None", command=self.select_no_projects).pack(
            side=tk.LEFT, padx=(4, 0)
        )
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
            text="Hide fully cleaned",
            variable=self.hide_clean,
            command=self._apply_project_filter,
        ).pack(side=tk.RIGHT)

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
        self.engines.bind("<Double-1>", lambda _e: self.launch_engine())
        self.engines.bind(
            "<<TreeviewSelect>>",
            lambda _e: (self._clear_selection(self.tree), self._refresh_summary()),
        )

        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.engines.yview)
        self.engines.configure(yscrollcommand=vsb.set)
        self.engines.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(6, 0))
        ttk.Button(buttons, text="Launch Engine", command=self.launch_engine).pack(
            side=tk.LEFT
        )

        # Split in two: reclaiming Intermediate never affects whether the
        # editor launches (it's the compile cache). Reclaiming Binaries does
        # -- the editor will not start until the engine is rebuilt. See the
        # class docstring / policy.ENGINE_TARGETS for the full reasoning.
        checks = ttk.Frame(frame)
        checks.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        ttk.Checkbutton(
            checks,
            text="Include Intermediate on source-built engines "
                 "(tens of GB; editor still opens)",
            variable=self.engine_intermediate,
            command=self.rescan,
        ).pack(side=tk.TOP, anchor=tk.W)
        ttk.Checkbutton(
            checks,
            text="Include Binaries on source-built engines "
                 "(editor will NOT launch until the engine is rebuilt)",
            variable=self.engine_binaries,
            command=self.rescan,
        ).pack(side=tk.TOP, anchor=tk.W)

    # ---------------------------------------------------------------- scanning

    def _clear_selection(self, tree: ttk.Treeview) -> None:
        if tree.selection():
            tree.selection_remove(*tree.selection())

    def rescan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        # Delete by tracked key, not tree.get_children() -- "Hide fully
        # cleaned" detaches rows rather than removing them, and get_children()
        # only returns attached (visible) items. Deleting only those would
        # leave detached rows behind with their old iids, which then collide
        # with the next scan's insert() for the same project.
        for key in list(self.reports):
            if self.tree.exists(key):
                self.tree.delete(key)
        for key in list(self.engine_reports):
            if self.engines.exists(key):
                self.engines.delete(key)
        self.reports.clear()
        self.engine_reports.clear()
        self.status.set("Searching...")
        self.summary.set("")
        engine_keys = frozenset(
            t.key for t in policy_mod.ENGINE_TARGETS if t.default_on
        ) | ({"engine_intermediate"} if self.engine_intermediate.get() else set()) \
          | ({"engine_binaries"} if self.engine_binaries.get() else set())
        base_policy = policy_mod.DEFAULT_POLICY.with_overrides(
            {"targets": [key for key, var in self.target_vars.items() if var.get()]}
        )
        threading.Thread(
            target=self._scan_worker, args=(engine_keys, base_policy), daemon=True
        ).start()

    def _scan_worker(
        self, engine_keys: frozenset[str], base_policy: policy_mod.Policy
    ) -> None:
        """Runs off the main thread; every result goes back through the queue."""
        try:
            engines = find_engine_installs()
            for engine in engines:
                self.results.put(("engine", scan_engine(engine, engine_keys)))
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
        if self.hide_clean.get() and report.reclaimable_bytes == 0:
            self.tree.detach(key)
        self._refresh_summary()

    def _apply_project_filter(self) -> None:
        """Detach (not delete) fully-cleaned rows so their data survives the toggle."""
        hide = self.hide_clean.get()
        visible = set(self.tree.get_children())
        for key, report in self.reports.items():
            is_clean = report.reclaimable_bytes == 0
            if hide and is_clean and key in visible:
                self.tree.detach(key)
            elif not (hide and is_clean) and key not in visible:
                # Reattach at the end; a header click re-sorts immediately after.
                self.tree.move(key, "", "end")

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
        eng_total = sum(r.reclaimable_bytes for r in self.engine_reports.values())
        selected_bytes = sum(r.reclaimable_bytes for r in self._eligible_selection())

        self.summary.set(
            f"{human(selected_bytes)} selected to reclaim  ·  "
            f"{human(proj_total + eng_total)} reclaimable found  ·  "
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

    def _eligible_selection(self) -> list[ProjectReport | EngineReport]:
        """Exactly what 'Clean Selected' would act on right now.

        Single source of truth for both the live summary figure and the
        actual clean action, so the number on screen can never drift from
        what clicking the button actually reclaims.
        """
        projects = [r for r in self._selected_projects() if self._tag(r) == "eligible"]
        engines = [r for r in self._selected_engines() if self._engine_tag(r) == "eligible"]
        return [*projects, *engines]

    def select_all_projects(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def select_no_projects(self) -> None:
        self.tree.selection_remove(*self.tree.selection())

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

    def _engine_editor_executable(self, engine_root: Path) -> Path | None:
        """The editor binary this engine install would launch, if it has one."""
        if sys.platform == "darwin":
            candidate = engine_root / "Engine/Binaries/Mac/UnrealEditor.app"
        elif sys.platform.startswith("win"):
            candidate = engine_root / "Engine/Binaries/Win64/UnrealEditor.exe"
        else:
            candidate = engine_root / "Engine/Binaries/Linux/UnrealEditor"
        return candidate if candidate.exists() else None

    def launch_engine(self) -> None:
        """Launch the editor with no project -- the engine's own project browser."""
        for report in self._selected_engines()[:1]:
            executable = self._engine_editor_executable(report.engine.root)
            if executable is None:
                messagebox.showerror(
                    "Can't launch",
                    f"No editor executable found under {report.engine.root}.\n\n"
                    "If this engine's Binaries were reclaimed, it needs a rebuild "
                    "before it can launch.",
                )
                return
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(executable)])
            elif sys.platform.startswith("win"):
                os.startfile(str(executable))  # noqa: S606
            else:
                subprocess.Popen([str(executable)])
            self.status.set(f"Launched {report.engine.label}")

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
        chosen = self._eligible_selection()
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
