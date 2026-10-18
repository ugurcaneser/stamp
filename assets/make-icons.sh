#!/bin/bash
# Regenerate the platform icons from assets/stamp-icon.svg (the single source).
# Needs rsvg-convert (brew install librsvg) and macOS's iconutil. The outputs
# are committed, so builds don't need either tool.

set -euo pipefail

ASSETS_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$ASSETS_DIR")"
SVG="$ASSETS_DIR/stamp-icon.svg"

ICONSET="$(mktemp -d)/AppIcon.iconset"
trap 'rm -rf "$(dirname "$ICONSET")"' EXIT
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$ICONSET/icon_${size}x${size}.png"
    rsvg-convert -w "$((size * 2))" -h "$((size * 2))" "$SVG" -o "$ICONSET/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$PROJECT_ROOT/macos/Resources/AppIcon.icns"
echo "Wrote macos/Resources/AppIcon.icns"
