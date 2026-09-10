#!/bin/bash
# Build stamp_<version>_all.deb.
#
# Uses dpkg-deb directly against a hand-assembled package tree rather than
# full debhelper/dh-python: this is a single, Ubuntu-only, pure-Python
# package with no compiled extensions, so the extra machinery buys nothing.
# packaging/debian/control and packaging/debian/postinst are the real
# sources of truth (no separate debian/rules build step to keep in sync).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

VERSION="$(grep -m1 '^Version:' "$SCRIPT_DIR/debian/control" | awk '{print $2}')"
PKG_ROOT="$BUILD_DIR/stamp_${VERSION}_all"

echo "Building stamp ${VERSION}..."

mkdir -p "$PKG_ROOT/DEBIAN"
cp "$SCRIPT_DIR/debian/control" "$PKG_ROOT/DEBIAN/control"
cp "$SCRIPT_DIR/debian/postinst" "$PKG_ROOT/DEBIAN/postinst"
chmod 0755 "$PKG_ROOT/DEBIAN/postinst"

mkdir -p "$PKG_ROOT/usr/lib/stamp"
cp -R "$PROJECT_ROOT/core" "$PKG_ROOT/usr/lib/stamp/core"
cp -R "$PROJECT_ROOT/gui" "$PKG_ROOT/usr/lib/stamp/gui"
cp -R "$PROJECT_ROOT/cli" "$PKG_ROOT/usr/lib/stamp/cli"
cp "$PROJECT_ROOT/launcher.py" "$PKG_ROOT/usr/lib/stamp/launcher.py"
cp "$PROJECT_ROOT/helper/stamp-helper" "$PKG_ROOT/usr/lib/stamp/stamp-helper"
chmod 0755 "$PKG_ROOT/usr/lib/stamp/stamp-helper"

# strip caches/tests from the installed copy
find "$PKG_ROOT/usr/lib/stamp" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

mkdir -p "$PKG_ROOT/usr/bin"
cat > "$PKG_ROOT/usr/bin/stamp" <<'EOF'
#!/bin/sh
exec python3 /usr/lib/stamp/launcher.py "$@"
EOF
chmod 0755 "$PKG_ROOT/usr/bin/stamp"

mkdir -p "$PKG_ROOT/usr/share/applications"
cp "$SCRIPT_DIR/stamp.desktop" "$PKG_ROOT/usr/share/applications/stamp.desktop"

mkdir -p "$PKG_ROOT/usr/share/polkit-1/actions"
cp "$SCRIPT_DIR/org.example.stamp.policy" "$PKG_ROOT/usr/share/polkit-1/actions/org.example.stamp.policy"

mkdir -p "$PKG_ROOT/usr/share/doc/stamp"
cp "$PROJECT_ROOT/README.md" "$PKG_ROOT/usr/share/doc/stamp/README.md"

OUTPUT_DIR="${1:-$PROJECT_ROOT/dist}"
mkdir -p "$OUTPUT_DIR"
dpkg-deb --build --root-owner-group "$PKG_ROOT" "$OUTPUT_DIR/stamp_${VERSION}_all.deb"

echo "Built $OUTPUT_DIR/stamp_${VERSION}_all.deb"
