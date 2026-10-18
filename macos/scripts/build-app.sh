#!/bin/bash
# Build Stamp.app (universal: arm64 + x86_64) and package it as a DMG.
#
# Signing and notarization are driven by environment variables:
#   STAMP_SIGN_IDENTITY   codesign identity. Default "-" (ad-hoc, local use only).
#                         For distribution: "Developer ID Application: <Name> (<TEAMID>)".
#   STAMP_NOTARY_PROFILE  notarytool keychain profile (xcrun notarytool store-credentials).
#                         When set, the DMG is notarized and stapled. Requires a
#                         Developer ID identity; ad-hoc builds can't be notarized.
#
# Output: dist/macos/Stamp.app and dist/macos/Stamp-<version>.dmg
# The version comes from packaging/debian/control so both platforms ship the same number.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$PACKAGE_DIR")"
OUT_DIR="${1:-$PROJECT_ROOT/dist/macos}"

VERSION="$(grep -m1 '^Version:' "$PROJECT_ROOT/packaging/debian/control" | awk '{print $2}')"
IDENTITY="${STAMP_SIGN_IDENTITY:--}"
NOTARY_PROFILE="${STAMP_NOTARY_PROFILE:-}"

if [ -n "$NOTARY_PROFILE" ] && [ "$IDENTITY" = "-" ]; then
    echo "error: notarization needs a Developer ID identity in STAMP_SIGN_IDENTITY" >&2
    exit 1
fi

echo "Building Stamp ${VERSION} for macOS..."
swift build -c release --arch arm64 --arch x86_64 --package-path "$PACKAGE_DIR"
BIN_DIR="$(swift build -c release --arch arm64 --arch x86_64 --package-path "$PACKAGE_DIR" --show-bin-path)"

APP="$OUT_DIR/Stamp.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/Stamp" "$APP/Contents/MacOS/Stamp"
sed "s/__VERSION__/${VERSION}/g" "$PACKAGE_DIR/Resources/Info.plist" > "$APP/Contents/Info.plist"
plutil -lint -s "$APP/Contents/Info.plist"

# Hardened runtime + secure timestamp are what notarization requires; ad-hoc
# signatures can't carry a timestamp.
SIGN_ARGS=(--force --options runtime --sign "$IDENTITY")
if [ "$IDENTITY" != "-" ]; then
    SIGN_ARGS+=(--timestamp)
fi
codesign "${SIGN_ARGS[@]}" "$APP"
codesign --verify --strict --verbose=2 "$APP"

DMG="$OUT_DIR/Stamp-${VERSION}.dmg"
rm -f "$DMG"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -quiet -volname "Stamp ${VERSION}" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
codesign "${SIGN_ARGS[@]}" "$DMG"

if [ -n "$NOTARY_PROFILE" ]; then
    echo "Submitting $DMG for notarization..."
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
    spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
fi

echo "Built $APP"
echo "Built $DMG"
