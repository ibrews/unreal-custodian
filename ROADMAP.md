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

### Cross-platform validation — CLI and GUI both ✅
43 tests passing on macOS and Windows. The Windows CLI run found six defects that local testing could not have (see the git log): `os.getuid()` doesn't exist there, `os.access` lies about directory writability, zero engines found until the launcher manifest was read directly, the no-index fallback walk didn't finish on a whole drive, an `AppData`-substring exclusion hid everything under a Windows temp dir, and `Build.version` search matched packaged games (two Fortnite installs) as "engines."

The GUI's first-ever Windows run (2026-08-09, via a sibling session on Lenovo over the fleet bus, since Archie was unreachable all round): clean launch, 10+ minute uptime with no crash, `MainWindowTitle` confirmed as "Unreal Custodian" via the OS itself — not just "the process didn't exit" — and an empty stderr, no traceback. No screenshot (desktop-control access was declined on that machine, unrelated to the app), but process-level + window-title confirmation from the OS is real evidence, not a guess.

### macOS blank-window bug — root-caused and fixed ✅
Apple ships exactly one tkinter-capable interpreter (`/usr/bin/python3`, Xcode's bundled Python 3.9), and its Tk is 8.5 — old enough to hit a known blank/white-window rendering bug on modern macOS. `brew install python-tk@3.12` (Tk 9) is the confirmed fix, documented in the README, checked for at GUI startup (prints a clear terminal warning instead of a silent empty window), and now also handled automatically by `packaging/macos/Unreal Custodian.app`, which searches for a good-Tk Python before launching.

### Standalone builds, both platforms ✅
The old `.app` wrapper exec'd whatever Python the user already had — fine from a clone, useless as a release download for someone with no Python at all. `packaging/macos/setup.py` (py2app) and `packaging/windows/build.bat` (PyInstaller, must run on real Windows — Archie was unreachable, a live session on Lenovo built and verified it, twice, catching two real bugs in `build.bat` in the process: an `--icon`/`--specpath` path-doubling `FileNotFoundError`, and a missing `errorlevel` check that let the script print "Built:" over a broken 281 KB stub) now produce genuine standalone binaries — both attached to the [v0.1.0 release](https://github.com/ibrews/unreal-custodian/releases/tag/v0.1.0). Signed **and notarized** on macOS with the Agile Lens Developer ID cert (Alex's call — no personal/ibrews cert exists); `spctl` confirms "accepted / Notarized Developer ID". Unsigned on Windows (no Authenticode infrastructure exists anywhere in the fleet).

**Bonus:** building a real py2app bundle also fixed the "Dock shows Python, not Unreal Custodian" issue below, for free — turns out proper packaging was the actual fix, not a workaround.

### macOS Dock identity — fixed by proper packaging ✅
Was: Framework Python's own tiny `Python.app` stub re-asserts its bundle identity (`org.python.python`) on startup regardless of what launched it, so the thin shell-wrapper `.app` always showed "Python" in the Dock no matter how it was invoked — confirmed by copying the interpreter binary to a plain file inside the wrapper with no other `.app` anywhere in its path, and watching it re-exec back to its real install path anyway. A shell-script wrapper genuinely cannot defeat that.

The py2app-built standalone app doesn't have this problem, because it isn't exec-ing into an *external* Python.app at all — its own Python is bundled inside its own `Contents/Frameworks/`. Verified: copied the built `.app` to `/tmp`, launched it from there with zero dependency on the dev checkout, and confirmed via `System Events` it reports as `Unreal Custodian` / `com.alexcoulombepresents.unreal-custodian` — not `Python` / `org.python.python`.

### macOS notarization ✅
Submitted, accepted, stapled. The Issuer ID came from the existing `asc_key.local` convention (found at `UnrealRealityKitBridge`, per the project docs' own pointer to "copy it from an existing one"). `spctl -a -vv` on the final build: `accepted` / `source=Notarized Developer ID`.

The first submission came back `Invalid` — `codesign --deep` on the outer bundle alone did not reliably sign everything; 52 nested `.so` files under `Contents/Resources/lib/python3.12/lib-dynload/` (the Python stdlib's C extensions) were still ad-hoc-signed with no timestamp, because `--deep`'s traversal is documented by Apple as unreliable for complex bundles and in practice missed loose binaries outside `Contents/Frameworks/`. Fixed by finding every actual Mach-O file in the bundle (`file` on each entry, not just `.so`/`.dylib` by extension) and signing each individually — hardened runtime, timestamped — before resealing the outer `.app`. Re-submitted, accepted on the second pass.

## Not done

### Windows code signing
No Authenticode certificate or signing infrastructure exists anywhere in the fleet for Windows binaries (only Apple Developer certs, for the macOS/iOS side). `UnrealCustodian.exe` ships unsigned — SmartScreen shows an "unknown publisher" click-through, a real but much smaller speed bump than macOS's Gatekeeper block on an unnotarized app. Getting a proper cert is a separate cost/setup decision, not started.

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
- **CI-built releases.** Both packaging builds are currently manual (and the Windows one needed real hardware this session didn't have direct access to). A GitHub Actions workflow building both platforms on tag push would remove the dependency on finding a live Windows session next time.
