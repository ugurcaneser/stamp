#!/bin/bash
# Build Stamp.exe for Windows: one self-contained, single-file x64 executable
# (no .NET install needed on the target). Runs on Windows (Git Bash, as in CI);
# the WPF part can't be built by the macOS/Linux .NET SDK.
#
# Output: dist/windows/Stamp-<version>-win-x64.exe
# The version comes from packaging/nfpm.yaml so every platform ships the same number.
# Unsigned for now: Windows SmartScreen shows "unknown publisher" on first run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WINDOWS_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$WINDOWS_DIR")"
OUT_DIR="${1:-$PROJECT_ROOT/dist/windows}"

VERSION="$(sed -n 's/^version: "\(.*\)"$/\1/p' "$PROJECT_ROOT/packaging/nfpm.yaml")"

echo "Building Stamp ${VERSION} for Windows..."
rm -rf "$OUT_DIR/publish"
dotnet publish "$WINDOWS_DIR/src/Stamp/Stamp.csproj" \
    -c Release -r win-x64 --self-contained true \
    -p:PublishSingleFile=true \
    -p:IncludeNativeLibrariesForSelfExtract=true \
    -p:EnableCompressionInSingleFile=true \
    -p:DebugType=none \
    -p:Version="$VERSION" \
    -o "$OUT_DIR/publish"

EXE="$OUT_DIR/Stamp-${VERSION}-win-x64.exe"
cp "$OUT_DIR/publish/Stamp.exe" "$EXE"
echo "Built $EXE"
