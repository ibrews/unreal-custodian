#!/bin/bash
# Signs, notarizes, and staples dist/Unreal Custodian.app, then re-zips it for
# distribution. Run after `setup.py py2app` produces a fresh build.
#
# Usage:
#   packaging/macos/sign_and_notarize.sh <codesign-identity-sha1-or-name> \
#       <asc-key-id> <asc-issuer-id> <path-to-.p8>
#
# The identity must be a "Developer ID Application" certificate -- "Apple
# Development" certs can't notarize. The ASC API key needs at least the
# "Developer" role in App Store Connect.
set -euo pipefail

IDENTITY="${1:?codesign identity required}"
KEY_ID="${2:?ASC key ID required}"
ISSUER="${3:?ASC issuer ID required}"
KEY_PATH="${4:?path to .p8 key required}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HERE/dist/Unreal Custodian.app"
ZIP="$HERE/dist/Unreal Custodian (macOS).zip"

[ -d "$APP" ] || { echo "Not found: $APP -- run setup.py py2app first" >&2; exit 1; }

# codesign --deep on the outer bundle alone is NOT sufficient for a py2app
# build: Apple's own docs describe --deep's traversal as unreliable for
# complex bundles, and in practice it misses loose binaries living outside
# Contents/Frameworks/. Confirmed the hard way (2026-08-09): the first
# notarization submission came back "Invalid" with 52 unsigned .so files
# under Contents/Resources/lib/python3.12/lib-dynload/ -- the Python stdlib's
# C extensions, which py2app drops in Resources/, not Frameworks/. The only
# reliable fix is finding every actual Mach-O file (via `file`, not just
# matching .so/.dylib by extension -- catches anything else py2app or a
# future dependency might add) and signing each one explicitly, before
# resealing the outer .app.
echo "Finding every Mach-O binary in the bundle..."
MACHO_LIST="$(mktemp)"
trap 'rm -f "$MACHO_LIST"' EXIT
find "$APP" -type f -print0 | while IFS= read -r -d '' f; do
  file -b "$f" 2>/dev/null | grep -q "Mach-O" && printf '%s\n' "$f"
done > "$MACHO_LIST"
echo "  found $(wc -l < "$MACHO_LIST" | tr -d ' ') Mach-O files"

echo "Signing each individually (hardened runtime, timestamped)..."
while IFS= read -r f; do
  codesign --force --timestamp --options runtime --sign "$IDENTITY" "$f"
done < "$MACHO_LIST"

echo "Resealing the outer bundle..."
codesign --force --timestamp --options runtime --sign "$IDENTITY" "$APP"

echo "Verifying..."
codesign --verify --deep --strict "$APP"

echo "Zipping for submission..."
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "Submitting for notarization (this waits for Apple, usually a few minutes)..."
xcrun notarytool submit "$ZIP" --key "$KEY_PATH" --key-id "$KEY_ID" --issuer "$ISSUER" --wait

echo "Stapling..."
xcrun stapler staple "$APP"

echo "Re-zipping the stapled app for distribution..."
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "Final Gatekeeper check:"
spctl -a -vv "$APP"

echo
echo "Done: $ZIP"
