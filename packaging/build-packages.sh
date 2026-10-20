#!/bin/bash
# Build the Linux packages into OUTPUT_DIR (default: dist/):
#   stamp_<version>_all.deb and stamp-<version>-1.noarch.rpm
# Arch users get the AUR package stamp-sd (packaging/arch/PKGBUILD) instead:
# an Arch package named "stamp" would collide with the unrelated AUR "stamp".
# Pass package formats after the output dir to build only some of them, e.g.
# `build-packages.sh dist deb`. Needs nfpm (https://nfpm.goreleaser.com).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$(mkdir -p "${1:-$PROJECT_ROOT/dist}" && cd "${1:-$PROJECT_ROOT/dist}" && pwd)"
shift || true
FORMATS=("$@")
[ ${#FORMATS[@]} -gt 0 ] || FORMATS=(deb rpm)

command -v nfpm >/dev/null || { echo "nfpm is not installed (https://nfpm.goreleaser.com)" >&2; exit 1; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
"$SCRIPT_DIR/stage.sh" "$WORK_DIR/root"
cp "$SCRIPT_DIR/nfpm.yaml" "$SCRIPT_DIR/postinstall.sh" "$WORK_DIR/"

VERSION="$(sed -n 's/^version: "\(.*\)"$/\1/p' "$SCRIPT_DIR/nfpm.yaml")"
echo "Building stamp ${VERSION}..."

cd "$WORK_DIR"
for format in "${FORMATS[@]}"; do
    case "$format" in
        deb) target="stamp_${VERSION}_all.deb" ;;
        rpm) target="stamp-${VERSION}-1.noarch.rpm" ;;
        *) echo "unknown package format: $format" >&2; exit 1 ;;
    esac
    nfpm package --config nfpm.yaml --packager "$format" --target "$OUTPUT_DIR/$target" >/dev/null
    echo "Built $OUTPUT_DIR/$target"
done
