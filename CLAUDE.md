# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Stamp formats a removable SD card/USB drive as **forced FAT32** (`mkfs.vfat -F 32`, even above 32GB) and copies a zip's contents onto it. Target: Ubuntu 22.04+/24.04+ on GNOME, GTK4 + libadwaita GUI plus a `--cli` mode. `STAMP_APP_SPEC.md` is the original spec (section refs like "§4.3" in code point there); `ARCHITECTURE.md` explains the privilege-separation design in depth.

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                                   # full suite (config in pyproject.toml, no PYTHONPATH needed)
.venv/bin/pytest tests/test_safety.py::test_name   # single test
python -m compileall -q launcher.py cli core gui   # CI syntax check
bash -n helper/stamp-helper packaging/stage.sh packaging/build-packages.sh packaging/postinstall.sh
./packaging/build-packages.sh                      # -> dist/ .deb + .rpm (needs nfpm)
python3 launcher.py --cli list                     # run CLI from source; no args = GUI
```

macOS app (Swift package in `macos/`):

```sh
swift test --package-path macos                                   # XCTest suite
swift test --package-path macos --filter SafetyTests/testRemovableCardIsAllowed
swift run --package-path macos Stamp                              # run the app from source
./macos/scripts/build-app.sh                                      # dist/macos/Stamp.app + DMG; STAMP_SIGN_IDENTITY / STAMP_NOTARY_PROFILE for Developer ID + notarization
```

Windows app (.NET 8 solution in `windows/`; `brew install dotnet@8 dosfstools mtools` on a Mac):

```sh
dotnet test windows/tests/StampCore.Tests                                 # xUnit suite, runs on macOS/Linux
STAMP_REQUIRE_FAT_TOOLS=1 dotnet test windows/tests/StampCore.Tests       # fail instead of skipping when fsck.fat/mtools are missing
./windows/scripts/build-exe.sh                                            # dist/windows/Stamp-<version>-win-x64.exe; Windows only (no WindowsDesktop SDK elsewhere)
```

CI (`.github/workflows/ci.yml`) only runs when started by hand (`workflow_dispatch`: `gh workflow run ci.yml`), not on push, to save Actions minutes; verify locally first. It runs pytest on Python 3.10 and 3.12, the compile/`bash -n` checks, the `.deb`/`.rpm` build (artifact `stamp-linux`) plus an install check of each in Ubuntu 22.04/24.04, Debian 12 and Fedora containers and a `makepkg` + install of the AUR PKGBUILD in Arch, on macOS `swift test` plus an ad-hoc-signed app/DMG build, the Windows core tests on Linux with dosfstools/mtools, and on Windows the tests plus the `.exe` build (uploaded as the `stamp-windows` artifact). Keep code compatible with Python 3.10, the Swift package at tools 5.10 / macOS 14, and .NET 8.

The app icon's single source is `assets/stamp-icon.svg`; `assets/make-icons.sh` regenerates the committed `macos/Resources/AppIcon.icns` and `windows/src/Stamp/Stamp.ico`/`Logo.png` (needs `rsvg-convert`), and `packaging/stage.sh` installs the SVG as the hicolor icon. The GTK app ID, desktop file and icon name are all `com.ugurcaneser.Stamp` so GNOME can match the window to them; the polkit action IDs use `com.ugurcaneser.stamp`.

Linux packaging: `packaging/stage.sh DESTDIR` is the single description of the installed file tree; `build-packages.sh` stages it and runs nfpm with `packaging/nfpm.yaml` (metadata plus per-format dependency names) to produce the `.deb` and `.rpm`, and `packaging/arch/PKGBUILD` calls `stage.sh` from the release tarball. Arch is served only through the AUR package `stamp-sd` (the AUR's `stamp` is an unrelated GNOME app, so an Arch package named `stamp` would be "upgraded" to it by AUR helpers); `packaging/arch/.SRCINFO` must be regenerated (`makepkg --printsrcinfo`) whenever the PKGBUILD changes. Keep the dependency lists in `nfpm.yaml` and the PKGBUILD in step.

The release version lives in `packaging/nfpm.yaml` (`version:`); `build-packages.sh`, `macos/scripts/build-app.sh` and `windows/scripts/build-exe.sh` all read it from there. `pkgver` in `packaging/arch/PKGBUILD` (CI checks it matches) and the README's install examples also hardcode it.

## Architecture

- `launcher.py` is the single entry point (`/usr/bin/stamp`): `--cli` as the first arg dispatches to `cli/main.py`, otherwise `gui/app.py`. GTK is only imported on the GUI path.
- `core/` holds all logic and must not import GTK. `cli/` and `gui/` are thin consumers of the same `core/` functions; don't duplicate logic in either. `core/formatter.py` may use `gi.repository.UDisks`/`GLib` (D-Bus bindings), imported lazily inside functions.
- **Privilege split** (see ARCHITECTURE.md table): enumeration via `lsblk`/`findmnt` subprocesses as the user; unmounting existing partitions via udisks2 D-Bus; mounting/unmounting the new partition via `udisksctl`; zip extract + `rsync` as the user. Only partitioning + `mkfs.vfat -F 32` runs as root, through `helper/stamp-helper` via `pkexec` (udisks2's `Block.Format()` can't force FAT32). The helper must stay minimal: one whitelisted subcommand, and it re-validates the device path, block-device check, root-disk check and label on its own, independently of `core/`. Changes to validation in `core/safety.py`/`core/formatter.py` (e.g. `_DEVICE_PATH_RE`, `sanitize_label`) should be mirrored in the helper.
- **Two helper paths, one script:** `packaging/stage.sh` installs it as `/usr/lib/stamp/stamp-helper` (polkit `allow_active: yes`, so no password, and the helper refuses anything without lsblk `RM=1`) and `/usr/lib/stamp/stamp-helper-unrestricted` (`auth_admin`, used for the non-removable override). The helper picks its mode from `basename "$0"`, never from an argument. `format_device` passes `unrestricted=not device.removable`, so keep the removable criterion identical in `core/device_scanner.py` and the helper. Policy actions in `packaging/com.ugurcaneser.stamp.policy` are tied to these exact paths. Outside the installed package only the removable-only `helper/stamp-helper` exists (`_resolve_helper_path`).
- `udisksctl mount` deliberately runs without `--no-user-interaction` so the non-removable override can show a polkit prompt.
- **Safety**: `core/safety.py` blocks the disk backing `/` and any disk with a partition mounted at system paths (`/boot`, `/home`, `/var`, …) with no override. Non-removable devices are refused unless explicitly allowed (`--allow-non-removable` in the CLI, a risk checkbox in the GUI's single confirm dialog). The GUI uses one summary confirm dialog (`gui/confirm_dialog.py`); recent commits deliberately removed extra acknowledgement steps, so don't add new ones.
- **Flow**: format → extract → mount → copy (rsync) → sync → unmount. In the GUI this runs on a background `threading.Thread` in `gui/main_window.py`; UI updates must go through `GLib.idle_add`.
- Everything (device/zip choices, commands, outcomes) is logged to `~/.local/state/stamp/stamp.log` via `core/logging_setup.py` (logger name `"stamp"`).

## macOS app

- A separate native SwiftUI app, not shared code. `macos/Sources/StampCore` mirrors `core/` module for module (`DiskScanner`, `Safety`, `Formatter`, `ContentWriter`, plus `StampJob` for the whole flow) and must not import SwiftUI/AppKit. Only `macos/Sources/Stamp` is UI. **Safety rules exist twice** (`core/safety.py` and `Safety.swift`, and label sanitizing in both formatters), so change them on both sides together.
- No privileged helper: `diskutil eraseDisk FAT32 <LABEL> MBRFormat /dev/diskN` forces FAT32 and doesn't need admin for external disks. All external tools run through the injectable `CommandRunning` protocol; tests use `FakeRunner` with canned `diskutil` plists (`Tests/StampCoreTests/Support.swift` holds a real Apple-silicon disk layout) and the real `/usr/bin/unzip`.
- The boot disk is detected by attributing APFS container volumes and snapshot mounts back to their `APFSPhysicalStores`, because `/` lives on a synthesized disk. Disk images report `RemovableMedia` and must stay filtered out (`VirtualOrPhysical`/`BusProtocol`).
- Don't use Foundation's `contentsOfDirectory`/`enumerator` for card or zip contents: they hide `._` files. Use `ContentWriter.walk`/`names` (readdir/lstat). Copies are `copyfile(COPYFILE_DATA)` so no xattrs/AppleDouble land on FAT.
- Unlike the Linux worker, the zip is extracted before the erase, and the disk is re-scanned and refused if its size or model changed since confirmation.

## Windows app

- A separate native WPF app. `windows/src/StampCore` mirrors the macOS `StampCore` (`DiskScanner`, `Safety`, `Fat32`, `DiskTools`, `ContentWriter`, `StampJob`) and targets plain `net8.0` so it builds and tests anywhere; only `windows/src/Stamp` is WPF. **Safety rules and label sanitizing now exist three times** (`core/`, `Safety.swift`/`Formatter.swift`, `Safety.cs`/`Fat32.cs`), so change them together.
- Windows can't force FAT32 above 32 GB, so `Fat32.cs` writes the file system itself onto the raw partition; `diskpart` only partitions. The FAT size comes from the exact `F*B/4 >= clusters+2` bound (fat32format's formula is one entry short at some sizes). Keep every write sector-aligned: raw volume handles require it (`WritesAreSectorAligned` test).
- The app runs elevated (`app.manifest` `requireAdministrator`) because diskpart and raw volume writes need it; there is no separate helper.
- Disks come from one PowerShell script (`Get-Disk` + `Win32_DiskDrive` + `Get-Partition`) passed with `-EncodedCommand`; tests feed canned JSON (`Fixtures/get-disk.json`) through `FakeRunner`. Disk-touching steps sit behind `IDiskTools` (`FakeDiskTools` in tests).
- Zip extraction skips Unix symlink entries itself (.NET would write them as text files) and guards against paths escaping the destination.

## Testing

- Python tests cover `core/` and `cli/` only; the dev venv has no `gi`, so `gui/` isn't unit-tested. The Swift tests cover `StampCore` only, and the .NET tests cover `windows/src/StampCore` only.
- `lsblk`/`findmnt` output is mocked. udisks2/`pkexec`/subprocess calls in `core/formatter.py` are tested through injected callables (`UnmountFn`, `HelperInvoker`, `CommandRunner`, `PathExistsFn`); keep new side-effecting code injectable the same way. `content_writer.py` runs real `rsync` against temp dirs.
- Real udisks2/PolicyKit/`parted`/`mkfs.vfat` behavior can only be checked on a real Ubuntu machine with a disposable card, real `diskutil` erase/mount/eject only on a Mac with a disposable card, and real diskpart/raw-volume/eject behavior (and the WPF UI itself) only on a Windows PC with a disposable card. Don't claim any of these paths is verified based on unit tests.

## Out of scope

Platforms other than Linux (Ubuntu/Debian and Fedora packages, Arch via the AUR; Ubuntu on GNOME is the primary target), macOS and Windows. Flatpak/Snap/AppImage: they can't ship the polkit policy and root helper the Linux design relies on. Blueprint/`.ui` files: widgets are built in Python on purpose. debhelper/dh-python/rpm spec files: nfpm builds the packages from the `stage.sh` tree.
