# Packaging

Two independent build pipelines, one per platform. Both produce a genuinely
standalone app — Python and Tk embedded, no requirement that the end user has
Python installed — which is different from the thin `Unreal Custodian.app`
shell wrapper also in `packaging/macos/` (that one execs whatever Python the
person running it already has; it's for someone running from a cloned
checkout, not a release download).

## macOS — py2app

```bash
cd packaging/macos
python3 -m venv .build-venv && .build-venv/bin/pip install py2app
.build-venv/bin/python setup.py py2app
```

Produces `packaging/macos/dist/Unreal Custodian.app`. Must run on macOS.

To sign, notarize, and staple in one step (Developer ID Application cert and
an App Store Connect API key with at least the Developer role required):

```bash
./sign_and_notarize.sh "<codesign identity>" "<ASC key ID>" "<ASC issuer ID>" "<path to .p8>"
```

**Do not just run `codesign --deep` on the outer bundle and call it done.**
Confirmed the hard way: Apple's own docs describe `--deep`'s traversal as
unreliable for complex bundles, and on a py2app build it misses the Python
stdlib's C extensions (`Contents/Resources/lib/python3.12/lib-dynload/*.so`)
because they live outside `Contents/Frameworks/`. The first notarization
submission with `--deep` alone came back `Invalid` with 52 unsigned files.
`sign_and_notarize.sh` finds every actual Mach-O binary in the bundle (via
`file`, not extension matching) and signs each one explicitly before
resealing the outer `.app` — that's the only reliable way for this kind of
bundle. It also submits, waits, staples, and re-zips for you.

Regenerate the icon with `./build_icon.sh` after editing `icon_source.html` —
it writes `icon.icns` here, which both this build and the thin wrapper .app
read from.

## Windows — PyInstaller

Must run **on Windows** — PyInstaller cannot cross-compile.

```powershell
py -3 -m pip install --upgrade pyinstaller
packaging\windows\build.bat
```

Produces `packaging\windows\dist\UnrealCustodian.exe`. Unsigned by default —
Windows SmartScreen will show an "unknown publisher" warning on first run
(a speed bump, not a hard block, unlike unsigned macOS apps). Authenticode
signing is a separate cost/setup item, not wired up here.

`icon.ico` is generated from the same `icon_source.html` as the macOS icon
(via Pillow: `Image.save(..., format="ICO", sizes=[...])`), so both
platforms' icons stay in sync by construction.
