# Stamp

Formats a removable SD card or USB drive as **FAT32** — explicitly forcing
FAT32 on cards larger than 32GB, which most stock GUI formatters (GParted's
default dialog, Windows' Format tool) silently refuse or cap — then copies
the contents of a zip file you provide onto it.

Targets Linux (Ubuntu 22.04+/24.04+ on GNOME first, with Debian, Fedora and
Arch packages too; GTK4 + libadwaita GUI, plus a `--cli`
mode for scripting), macOS 14+ (native SwiftUI app in `macos/`, see
[macOS](#macos) below) and Windows 10/11 (native WPF app in `windows/`, see
[Windows](#windows) below). See `STAMP_APP_SPEC.md` for the full specification
this was built from, and `ARCHITECTURE.md` for the privilege-separation design.

## Install

Each release has a package for the common Linux families (Ubuntu 22.04+,
Debian 12+, Fedora, Arch):

```sh
sudo apt install ./stamp_1.5.1_all.deb          # Ubuntu / Debian
sudo dnf install ./stamp-1.5.1-1.noarch.rpm     # Fedora
sudo pacman -U ./stamp-1.5.1-1-any.pkg.tar.zst  # Arch
```

To build them yourself (needs [nfpm](https://nfpm.goreleaser.com)):

```sh
./packaging/build-packages.sh     # writes all three into dist/
./packaging/build-packages.sh dist deb   # only the .deb
```

`packaging/arch/PKGBUILD` builds the same package from the release's source
tarball, for the AUR.

## Run

```sh
stamp                 # GUI
stamp --cli list       # list candidate removable devices
stamp --cli prepare --device /dev/sdb --zip firmware.zip --label BOOT
```

`stamp --cli prepare --help` for all flags (`--yes` to skip the interactive
confirmation prompt for scripting, `--allow-non-removable` to override the
"not flagged removable" refusal — DANGEROUS, double-check the device path).

## Safety model (read this before formatting anything)

- The disk backing `/` (or any disk with a partition mounted at `/`,
  `/boot`, `/home`, `/var`, `/usr`, `/etc`, `/opt`, `/srv`) is never listed
  and can never be selected, with no override.
- Formatting uses one concise summary dialog with a destructive "Format & Copy"
  action; removable devices do not require an additional erase acknowledgement.
- A device not flagged removable adds one explicit risk checkbox to that same
  dialog; it does not add another popup. Root/system disks remain blocked.
- No password prompt for removable devices in the active local session: the
  privileged helper's polkit action is `allow_active: yes`, and the helper
  itself refuses anything lsblk doesn't flag removable. The non-removable
  override goes through a second, password-protected copy of the helper.
  Trade-off: any program running in your session can format a removable
  device without asking.
- Every device/zip choice, command run, and outcome is logged to
  `~/.local/state/stamp/stamp.log`.

See `core/safety.py` for the actual logic and `tests/test_safety.py` for
what's covered.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -v
```

Pytest configuration lives in `pyproject.toml`, so tests resolve the local
packages without a manually-set `PYTHONPATH`. GitHub Actions (started by hand, `gh workflow run ci.yml`) runs the suite on
Python 3.10 (Ubuntu 22.04's baseline) and Python 3.12 (Ubuntu 24.04's baseline),
checks Python and shell syntax, builds the `.deb`, `.rpm` and Arch packages,
installs each in a Ubuntu 22.04/24.04, Debian 12, Fedora and Arch container
to check that every declared dependency resolves, and builds the AUR
`PKGBUILD`.

`core/` has no GTK imports and is fully unit-testable without a display —
device/lsblk parsing and safety logic are tested against mocked
`lsblk`/`findmnt` output, and zip/rsync logic runs for real against temp
directories. `formatter.py`'s udisks2 D-Bus and `pkexec` calls are tested
via dependency-injected fakes (see `tests/test_formatter.py`); actually
exercising real udisks2/pkexec/`parted`/`mkfs.vfat` needs a real Ubuntu
machine with a disposable card — there is no way to safely simulate that in
unit tests, so treat that path as reviewed-but-not-integration-tested until
run once for real.

The GUI (`gui/`) was built and manually exercised against a real GTK4 4.22 /
libadwaita 1.9 runtime (installed via Homebrew on macOS for development —
device picker, zip loading, the confirm-dialog gating logic, and the full
background-worker call order were all constructed and driven directly in a
Python shell and behaved correctly), but it has not been run inside an
actual GNOME session end-to-end, and udisks2/PolicyKit prompts can only be
verified on Linux. Before shipping, run it once on a real Ubuntu 22.04+/24.04+
box with a real (disposable) SD card and walk the full flow.

## macOS

A native SwiftUI app with the same flow and safety rules, in `macos/` (a
Swift package: `StampCore` library, `Stamp` app, `StampCoreTests`).

A signed, notarized `Stamp-<version>.dmg` is attached to each GitHub release
from v1.3.0 on.

```sh
swift test --package-path macos            # unit tests
swift run --package-path macos Stamp       # run from source
./macos/scripts/build-app.sh               # dist/macos/Stamp.app + Stamp-<version>.dmg (ad-hoc signed)
```

For a distributable build, sign with a Developer ID and notarize:

```sh
xcrun notarytool store-credentials stamp-notary   # once: Apple ID + app-specific password, or an API key
STAMP_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
STAMP_NOTARY_PROFILE=stamp-notary \
./macos/scripts/build-app.sh
```

How it differs from the Linux build:

- Everything goes through `diskutil` (`eraseDisk FAT32 <LABEL> MBRFormat`,
  which forces FAT32 at any card size). diskutil erases external disks for
  the logged-in user without an admin password, so there is no privileged
  helper and no password prompt at all.
- The zip is checked and extracted *before* the card is erased, so a bad zip
  never costs a wipe.
- Files are copied data-only (no extended attributes), and the folders and
  `._` files macOS adds to FAT volumes on its own (`.Spotlight-V100`,
  `.Trashes`, `.fseventsd`, AppleDouble files) are removed before ejecting.
  macOS may still recreate `.fseventsd` at eject time.
- The card is ejected at the end instead of just unmounted.
- Log: `~/Library/Logs/Stamp/stamp.log`.
- The first copy triggers macOS's one-time "access files on a removable
  volume" permission prompt.

Same caveat as the Linux side: the `diskutil` erase/mount/eject path is unit
tested against faked `diskutil` output only. Run it once on a real Mac with a
disposable card before trusting it.

## Windows

A native WPF app (.NET 8) with the same flow and safety rules, in `windows/`
(`StampCore` library, `Stamp` app, `StampCore.Tests`). Each GitHub release
from v1.5.0 on has a self-contained `Stamp-<version>-win-x64.exe` (no .NET
install needed).

```sh
dotnet test windows/tests/StampCore.Tests   # unit tests; runs on macOS/Linux too
./windows/scripts/build-exe.sh              # dist/windows/Stamp-<version>-win-x64.exe (Windows only)
```

How it differs from the other builds:

- Windows' own tools (`format`, `diskpart`, Explorer) refuse FAT32 above
  32 GB, so Stamp writes the FAT32 structures itself (boot sector, FSInfo,
  both FATs, root directory), like fat32format and Rufus. `diskpart` only
  creates the MBR partition table (`clean`, one type `0x0C` partition at
  1 MiB). The formatter is plain C# and is tested by formatting disk images
  and checking them with `fsck.fat` and mtools.
- That needs raw disk access, so the app asks for administrator rights
  (one UAC prompt) when it starts.
- "Removable" means Win32_DiskDrive `MediaType = Removable Media`. The disk
  holding Windows (boot/system flags or the `%SystemDrive%` letter) and
  virtual disks are never listed; disks over 2 TB are refused (MBR limit).
- Like the macOS app, the zip is extracted before the card is erased, and
  the disk is re-scanned and refused if its size, model or serial number
  changed since confirmation.
- Unsigned for now: SmartScreen warns about an unknown publisher on first
  run ("More info" → "Run anyway").
- Windows may show "You need to format the disk" for a moment while the
  new partition is still empty; ignore it, Stamp formats it right after.
- Log: `%LOCALAPPDATA%\Stamp\stamp.log`.

The WPF app and the diskpart/raw-volume path have only been built in CI.
Run it once on a real Windows PC with a disposable card before trusting it.

## What's deliberately not here

- Non-Ubuntu distros: out of scope per the spec (§8). macOS and Windows
  were added later as separate native apps.
- `gui/ui/*.ui` Blueprint/Cambalache files: widgets are built
  programmatically in Python instead. This is a deliberate deviation from
  the spec's suggested layout — Blueprint files can't be compiled/validated
  without a Linux toolchain, and programmatic construction was something
  that could actually be built and driven against a real GTK4 runtime here.
- Full debhelper/dh-python or rpm spec packaging: `stage.sh` assembles the
  file tree and nfpm wraps it as `.deb`, `.rpm` and Arch packages. Simpler
  and fully verifiable locally; reasonable to upgrade later if this is ever
  submitted to an actual distribution archive.
