# Roadmap

Status as of 2026-08-07. Phases follow the original plan: prove it's safe, then automate it, then make it configurable, then give it a face.

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
`python3 -m custodian.gui`. Sortable table, engine list, launch/reveal, "Never clean this" writes the opt-out, Trash-vs-permanent checkbox. Scanning runs on a worker thread and streams results in. Engine installs are independently selectable and cleanable, not just displayed — with the same "Include rebuildable Intermediate/Binaries" gate the CLI has behind `--engine-rebuildable`. Screenshot verified in the README.

### Engine installs ✅
Not in the original plan, and it turned out to be the bigger half — a single source-built engine held more reclaimable output than an entire 96-project library. Gated on `InstalledBuild.txt` vs `SourceDistribution.txt` so a precompiled install never gives up its `Binaries`.

### Cross-platform validation ✅
43 tests passing on macOS and Windows. The Windows run found six defects that local testing could not have (see the git log).

## Not done

### macOS: the only Apple-provided Python has a broken Tk
Apple ships exactly one tkinter-capable interpreter (`/usr/bin/python3`, Xcode's bundled Python 3.9), and its Tk is 8.5 — old enough to hit a known blank/white-window rendering bug on modern macOS: the GUI launches, the process is healthy, no error is logged, and the window simply never draws its contents. This was found live, not by inspection — the window rendered blank on screen while every earlier attempt to verify it had been capturing the *wrong window entirely* and reporting success. `brew install python-tk@3.12` (Tk 9) is the confirmed fix and is now documented in the README, but the tool does not detect or warn about an old Tk itself, so a user without Homebrew still hits this blind. Worth a startup check that names the problem instead of leaving them looking at an empty window.

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
