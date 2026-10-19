#!/bin/bash
# Regenerate the platform icons from assets/stamp-icon.svg (the single source).
# Needs rsvg-convert (brew install librsvg), python3 and macOS's iconutil. The
# outputs are committed, so builds don't need any of them.

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

# Windows .ico: PNG-compressed entries (supported since Vista), packed by hand.
ICO_DIR="$(dirname "$ICONSET")/ico"
mkdir -p "$ICO_DIR"
for size in 16 24 32 48 64 128 256; do
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$ICO_DIR/$size.png"
done
python3 - "$ICO_DIR" "$PROJECT_ROOT/windows/src/Stamp/Stamp.ico" <<'PY'
import struct, sys, pathlib
src, out = pathlib.Path(sys.argv[1]), sys.argv[2]
sizes = [16, 24, 32, 48, 64, 128, 256]
images = [(src / f"{s}.png").read_bytes() for s in sizes]
offset = 6 + 16 * len(images)
header = struct.pack("<HHH", 0, 1, len(images))
entries = b""
for size, data in zip(sizes, images):
    entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
pathlib.Path(out).write_bytes(header + entries + b"".join(images))
PY
echo "Wrote windows/src/Stamp/Stamp.ico"
# The header image inside the WPF window.
rsvg-convert -w 256 -h 256 "$SVG" -o "$PROJECT_ROOT/windows/src/Stamp/Logo.png"
echo "Wrote windows/src/Stamp/Logo.png"
