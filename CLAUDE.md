# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Stamp formats a removable SD card/USB drive as **forced FAT32** (`mkfs.vfat -F 32`, even above 32GB) and copies a zip's contents onto it. Target: Ubuntu 22.04+/24.04+ on GNOME, GTK4 + libadwaita GUI plus a `--cli` mode. `STAMP_APP_SPEC.md` is the original spec (section refs like "§4.3" in code point there); `ARCHITECTURE.md` explains the privilege-separation design in depth.

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                                   # full suite (config in pyproject.toml, no PYTHONPATH needed)
.venv/bin/pytest tests/test_safety.py::test_name   # single test
python -m compileall -q launcher.py cli core gui   # CI syntax check
bash -n helper/stamp-helper packaging/build-deb.sh packaging/debian/postinst
./packaging/build-deb.sh                           # -> dist/stamp_<version>_all.deb
python3 launcher.py --cli list                     # run CLI from source; no args = GUI
```

CI (`.github/workflows/ci.yml`) runs pytest on Python 3.10 and 3.12, the compile/`bash -n` checks, and a `.deb` build. Keep code compatible with Python 3.10.

The package version lives in `packaging/debian/control` (`build-deb.sh` reads it from there); the README's install example also hardcodes it.

## Architecture

- `launcher.py` is the single entry point (`/usr/bin/stamp`): `--cli` as the first arg dispatches to `cli/main.py`, otherwise `gui/app.py`. GTK is only imported on the GUI path.
- `core/` holds all logic and must not import GTK. `cli/` and `gui/` are thin consumers of the same `core/` functions; don't duplicate logic in either. `core/formatter.py` may use `gi.repository.UDisks`/`GLib` (D-Bus bindings), imported lazily inside functions.
- **Privilege split** (see ARCHITECTURE.md table): enumeration via `lsblk`/`findmnt` subprocesses as the user; unmounting existing partitions via udisks2 D-Bus; mounting/unmounting the new partition via `udisksctl`; zip extract + `rsync` as the user. Only partitioning + `mkfs.vfat -F 32` runs as root, through `helper/stamp-helper` via `pkexec` (udisks2's `Block.Format()` can't force FAT32). The helper must stay minimal: one whitelisted subcommand, and it re-validates the device path, block-device check, root-disk check and label on its own, independently of `core/`. Changes to validation in `core/safety.py`/`core/formatter.py` (e.g. `_DEVICE_PATH_RE`, `sanitize_label`) should be mirrored in the helper.
- The helper is found at `/usr/lib/stamp/stamp-helper` when installed, else `helper/stamp-helper` in the repo (`_resolve_helper_path`). The polkit action in `packaging/org.example.stamp.policy` is tied to the installed path.
- **Safety**: `core/safety.py` blocks the disk backing `/` and any disk with a partition mounted at system paths (`/boot`, `/home`, `/var`, …) with no override. Non-removable devices are refused unless explicitly allowed (`--allow-non-removable` in the CLI, a risk checkbox in the GUI's single confirm dialog). The GUI uses one summary confirm dialog (`gui/confirm_dialog.py`); recent commits deliberately removed extra acknowledgement steps, so don't add new ones.
- **Flow**: format → extract → mount → copy (rsync) → sync → unmount. In the GUI this runs on a background `threading.Thread` in `gui/main_window.py`; UI updates must go through `GLib.idle_add`.
- Everything (device/zip choices, commands, outcomes) is logged to `~/.local/state/stamp/stamp.log` via `core/logging_setup.py` (logger name `"stamp"`).

## Testing

- Tests cover `core/` and `cli/` only; the dev venv has no `gi`, so `gui/` isn't unit-tested.
- `lsblk`/`findmnt` output is mocked. udisks2/`pkexec`/subprocess calls in `core/formatter.py` are tested through injected callables (`UnmountFn`, `HelperInvoker`, `CommandRunner`, `PathExistsFn`); keep new side-effecting code injectable the same way. `content_writer.py` runs real `rsync` against temp dirs.
- Real udisks2/PolicyKit/`parted`/`mkfs.vfat` behavior can only be checked on a real Ubuntu machine with a disposable card. Don't claim that path is verified based on unit tests.

## Out of scope

Non-Ubuntu platforms. Blueprint/`.ui` files: widgets are built in Python on purpose. debhelper/dh-python: `build-deb.sh` calls `dpkg-deb --build` directly.
