#!/bin/bash
# Regenerates icon.icns from icon_source.html and drops it into the app
# bundle's Resources. Requires Chrome (for headless rendering) and macOS's
# built-in iconutil. Re-run this after editing icon_source.html.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
APP="$HERE/Unreal Custodian.app"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$CHROME" --headless --disable-gpu --hide-scrollbars \
  --window-size=1024,1024 --default-background-color=00000000 \
  --screenshot="$WORK/icon_1024.png" "file://$HERE/icon_source.html" >/dev/null 2>&1

ICONSET="$WORK/UnrealCustodian.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$WORK/icon_1024.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$WORK/icon_1024.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
cp "$WORK/icon_1024.png" "$ICONSET/icon_512x512@2x.png"

iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns"
echo "wrote $APP/Contents/Resources/icon.icns"
