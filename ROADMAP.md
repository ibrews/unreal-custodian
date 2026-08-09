# Roadmap

Status as of 2026-08-09. Phases follow the original plan: prove it's safe, then automate it, then make it configurable, then give it a face.

## Shipped

### Phase 0 — Read-only reporter ✅
`custodian report` inventories every project and engine install and prints what each is holding, with the rebuild cost of reclaiming it. Nothing is deleted; there is no flag that makes `report` destructive. This shipped first specifically so the safety rules could be validated against real machines before anything was removed.

### Phase 1 — Smoke test ✅
Verified end-to-end on two projects (`ThirdPersonClass` 8.7 GB → 359 MB, `GASP57` 7.4 GB → 5.1 GB), with the annotated Finder before/after in the README as evidence.

### Phase 2 — No-UI global ✅
`custodian clean`, dry-run by default, `--apply` to act. Disk-pressure trigger with age ranking, Trash by default and `--permanent` when space is actually needed, live-editor guard, writability pre-flight.

### Phase 3 — Per-project config ✅
`.ueclean.json` next to the `.uproject`, layered over global defaults, committable so a team shares one policy. Unknown target keys are a hard error rather than a silent no-op.

### Phase 4 — GUI ✅
`python3 -m custodian.gui`. Sortable table, engine list, launch/reveal, "Never clean this" writes the opt-out, Trash-vs-permanent checkbox. Scanning runs on a worker thread and streams results in. Engine installs are independently selectable and cleanable, not just displayed. Select All / Select None, "Hide fully cleaned" (detaches rather than deletes, so toggling it back never re-scans), a "Clean targets" checklist mirroring `.ueclean.json`'s `targets` field, and a **Clean Selected**/**Delete permanently** pair moved to a single bottom-right action bar shared by both tables, since the action already applied to either one. "N selected to reclaim" reads live off the current selection via `<<TreeviewSelect>>`, sharing one `_eligible_selection()` helper with the actual clean action so the number on screen can never drift from what clicking the button does. Layout is a draggable `ttk.PanedWindow`, not a fixed split. Screenshot verified in the README, from a real running window (window-ID capture, not a screen region — four earlier attempts had captured the wrong window and reported success).

### Engine Intermediate vs. Binaries are two different risks, split accordingly ✅
The original design bundled both behind one flag/checkbox with one "costs a full engine rebuild" warning. That overstated Intermediate's risk: it's purely the incremental-compile cache and reclaiming it never affects whether the editor launches, only whether the next compile is incremental. Binaries genuinely does block the editor. CLI: `--engine-intermediate` / `--engine-binaries`, replacing `--engine-rebuildable`. GUI: two independent checkboxes with accurate, distinct language. `cmd_clean` also now actually reclaims engines — previously only `report` could see them; `clean` silently could not touch an engine at all.

### Engine installs ✅
Not in the original plan, and it turned out to be the bigger half — a single source-built engine held more reclaimable output than an entire 96-project library. Gated on `InstalledBuild.txt` vs `SourceDistribution.txt` so a precompiled install never gives up its `Binaries`.

### Cross-platform validation — CLI ✅, GUI still open (see below)
43 tests passing on macOS and Windows. The Windows run found six defects that local testing could not have (see the git log): `os.getuid()` doesn't exist there, `os.access` lies about directory writability, zero engines found until the launcher manifest was read directly, the no-index fallback walk didn't finish on a whole drive, an `AppData`-substring exclusion hid everything under a Windows temp dir, and `Build.version` search matched packaged games (two Fortnite installs) as "engines."

### macOS blank-window bug — root-caused and fixed ✅
Apple ships exactly one tkinter-capable interpreter (`/usr/bin/python3`, Xcode's bundled Python 3.9), and its Tk is 8.5 — old enough to hit a known blank/white-window rendering bug on modern macOS. `brew install python-tk@3.12` (Tk 9) is the confirmed fix, documented in the README, checked for at GUI startup (prints a clear terminal warning instead of a silent empty window), and now also handled automatically by `packaging/macos/Unreal Custodian.app`, which searches for a good-Tk Python before launching.

## Not done

### macOS Dock still shows "Python", not "Unreal Custodian"
The `.app` bundle (`packaging/macos/Unreal Custodian.app`) fixes double-click launching and automatic Tk-version selection, but not this. Framework Python ships its own tiny `Python.app` stub so Tk can register with the window server, and that stub re-asserts its own bundle identity (`org.python.python`) to macOS on startup regardless of what launched it — confirmed by copying the interpreter binary to a plain file inside the wrapper bundle (no other `.app` anywhere in its path) and watching it re-exec back to its real Homebrew install path anyway. A shell-script wrapper can't defeat that. The real fix is `py2app`, a proper build step that produces a genuinely standalone bundle — not set up here yet. The window's own title bar is unaffected (`root.title()` always says "Unreal Custodian" correctly); only the Dock badge/tooltip is wrong.

### GUI never verified on Windows
Archie (the Windows test machine used for the CLI cross-platform pass) was unreachable for this entire round of work. The GUI's Windows-specific code paths (`os.startfile`, `explorer /select,`, the `Win64/UnrealEditor.exe` launch path) are written to the same pattern as the already-Windows-tested CLI code, but **"written correctly" is not the same claim as "verified running."** Nobody has actually launched `custodian.gui` on a Windows box. First thing to do next session.

### Scheduled automation
The README documents running `custodian clean --apply` from `launchd` or Task Scheduler, but there is no installer. Should be `custodian schedule --install` writing the plist / scheduled task, and `--uninstall`.

### Fab plugin
The marketplace-shaped deliverable: a UE Editor plugin (Fab category "Tools & Plugins") that manages your *other* projects from inside any open one — which sidesteps the fact that a plugin cannot clean the project it is running inside. Same core, different front end. Fab has no standalone-executable category, so this is the only route onto the marketplace.

### Windows Explorer before/after
The README has the annotated Finder capture. The equivalent Explorer capture is missing, and most Unreal developers are on Windows.

### Everything-absent UX
On a Windows machine without Everything running, discovery falls back to a whole-drive walk. It now completes (pruned + depth-capped) and warns, but it is still slow enough to be worth a progress indicator.

## Ideas, unscheduled

- **Restore.** Reclaimed directories go to the Trash with their structure intact; a `custodian restore <project>` that puts them back would make the tool much less scary.
- **Report to disk.** JSON/CSV output so it can feed a dashboard or a fleet-wide sweep.
- **Per-drive pressure.** Thresholds are currently evaluated against the volume holding `$HOME`; projects on other volumes should be judged against their own.
- **Cache the scan.** Sizes are recomputed every run; an mtime-invalidated cache would make repeat runs instant.
- **Prune deep, not wide.** `Saved/Screenshots` and `Saved/Autosaves` accumulate for years; per-file age policies rather than whole-directory ones.
- **py2app packaging.** Would fix the Dock identity issue above properly, and is also the standard path to a signed, notarized, distributable macOS build if this ever needs to leave "clone and run."
