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

To sign (Developer ID Application cert required — see README's "Support" /
project docs for which team):

```bash
codesign --deep --force --timestamp --options runtime \
  --sign "<certificate SHA-1 or exact name>" "dist/Unreal Custodian.app"
```

Then notarize (`xcrun notarytool submit ... --wait` on a zipped copy) and
staple (`xcrun stapler staple`) before distributing, or Gatekeeper still
rejects it on a fresh machine even though it's correctly signed.

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
