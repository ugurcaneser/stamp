#!/bin/bash
# Install Stamp's file tree under DESTDIR.
#
# The single description of what goes where: build-packages.sh stages into a
# temp dir and hands the tree to nfpm for .deb/.rpm, and the AUR PKGBUILD
# (stamp-sd) calls this with "$pkgdir". Pure Python + shell, nothing compiled.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DESTDIR="${1:?usage: stage.sh DESTDIR}"

# install -D is GNU-only; this also works with the BSD install on macOS.
put() {
    install -d "$(dirname "$3")"
    install -m "$1" "$2" "$3"
}

install -d "$DESTDIR/usr/lib/stamp"
cp -R "$PROJECT_ROOT/core" "$DESTDIR/usr/lib/stamp/core"
cp -R "$PROJECT_ROOT/gui" "$DESTDIR/usr/lib/stamp/gui"
cp -R "$PROJECT_ROOT/cli" "$DESTDIR/usr/lib/stamp/cli"
install -m 0644 "$PROJECT_ROOT/launcher.py" "$DESTDIR/usr/lib/stamp/launcher.py"
install -m 0755 "$PROJECT_ROOT/helper/stamp-helper" "$DESTDIR/usr/lib/stamp/stamp-helper"
# Second copy (not a symlink, so pkexec runs it under this exact path): the
# helper picks its mode from its own name, see the policy file.
install -m 0755 "$PROJECT_ROOT/helper/stamp-helper" "$DESTDIR/usr/lib/stamp/stamp-helper-unrestricted"

# strip caches from the installed copy
find "$DESTDIR/usr/lib/stamp" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$DESTDIR/usr/lib/stamp" -type f -name "*.py" -exec chmod 0644 {} +
find "$DESTDIR/usr/lib/stamp" -type d -exec chmod 0755 {} +

install -d "$DESTDIR/usr/bin"
cat > "$DESTDIR/usr/bin/stamp" <<'SH'
#!/bin/sh
exec python3 /usr/lib/stamp/launcher.py "$@"
SH
chmod 0755 "$DESTDIR/usr/bin/stamp"

# The desktop file and icon are named after the GTK application ID so GNOME
# matches the running window to them.
put 0644 "$SCRIPT_DIR/com.ugurcaneser.Stamp.desktop" "$DESTDIR/usr/share/applications/com.ugurcaneser.Stamp.desktop"
put 0644 "$PROJECT_ROOT/assets/stamp-icon.svg" "$DESTDIR/usr/share/icons/hicolor/scalable/apps/com.ugurcaneser.Stamp.svg"
put 0644 "$SCRIPT_DIR/com.ugurcaneser.stamp.policy" "$DESTDIR/usr/share/polkit-1/actions/com.ugurcaneser.stamp.policy"
put 0644 "$PROJECT_ROOT/README.md" "$DESTDIR/usr/share/doc/stamp/README.md"
put 0644 "$PROJECT_ROOT/LICENSE" "$DESTDIR/usr/share/doc/stamp/LICENSE"
