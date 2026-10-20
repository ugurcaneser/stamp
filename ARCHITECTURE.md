# Architecture

## Privilege separation (the decision the spec asked to be stated explicitly)

**Hybrid: udisks2 D-Bus for almost everything, a tiny `pkexec` helper for
exactly one step.**

| Operation | Mechanism | Runs as |
|---|---|---|
| Enumerate devices | `lsblk -J` (subprocess) | user |
| Determine root disk | `findmnt` + `lsblk -no PKNAME` (subprocess) | user |
| Unmount device's existing partitions before formatting | udisks2 D-Bus, `Filesystem.Unmount` via `gi.repository.UDisks` (`core/formatter.py:default_unmount_all`) | user (PolicyKit prompts in-session) |
| Create MBR partition table + one partition + `mkfs.vfat -F 32` | **`helper/stamp-helper` via `pkexec`** | root (no password for removable devices, see below) |
| Mount the freshly-formatted partition | `udisksctl mount -b` (subprocess) | user (no prompt for removable; PolicyKit prompts for the non-removable override) |
| Extract zip, copy files (`rsync`) | subprocess, no privilege needed | user |
| Unmount after copy | `udisksctl unmount -b` (subprocess) | user |

### Why the `mkfs.vfat -F 32` step needed `pkexec`, not pure udisks2

udisks2's `Block.Format()` D-Bus method — the call that would otherwise
create a partition table and filesystem for us entirely inside udisks2 —
does not expose an option to force the FAT bit width. Its options dictionary
supports things like `label` and `take-ownership`, but nothing equivalent to
dosfstools' `-F 32`. Left alone, `mkfs.fat` picks FAT16 vs FAT32 by its own
size-based heuristic.

Forcing FAT32 "regardless of card size" via `-F 32` is this app's entire
reason to exist (spec §1, §4.3) — the whole point is that stock tools get
this wrong or refuse outright above 32GB. Leaving it to auto-detection is
not an acceptable substitute for an app whose core value proposition is
guaranteed, explicit FAT32. So partitioning + `mkfs.vfat -F 32` goes through
`helper/stamp-helper`, invoked via `pkexec`, exactly as the spec's own §3
fallback clause pre-authorizes: *"If forcing `-F 32` FAT32 on very large
cards isn't achievable through udisks2's format call parameters, fall back
to a minimal privileged helper script invoked via `pkexec`... Keep this
fallback helper as small and auditable as possible."*

`helper/stamp-helper` does exactly that:
- One whitelisted subcommand (`format-fat32`).
- Its own independent input validation (device path shape, block-device
  check, root-disk re-check) — it does not trust that its caller already
  validated anything, since it's reachable directly via `pkexec` by anything
  on the system, not just Stamp's GUI/CLI.
- No shell interpolation of user-controlled strings into anything that
  reaches a shell (label is filtered to a safe charset with `tr -cd`, device
  path is matched against a fixed `case` pattern before use).
- Runs exactly four commands: `parted mklabel`, `parted mkpart`, `partprobe`,
  `mkfs.vfat -F 32`. Nothing else.

### No password for removable devices

`packaging/stage.sh` installs the helper twice, and
`packaging/com.ugurcaneser.stamp.policy` ties one PolicyKit action to each path
via the `org.freedesktop.policykit.exec.path` annotation:

| Installed path | Action | `allow_active` | Accepts |
|---|---|---|---|
| `/usr/lib/stamp/stamp-helper` | `com.ugurcaneser.stamp.format-fat32` | `yes` | only devices with lsblk `RM=1` |
| `/usr/lib/stamp/stamp-helper-unrestricted` | `com.ugurcaneser.stamp.format-fat32-unrestricted` | `auth_admin` | non-removable too |

The helper picks its mode from `basename "$0"`, i.e. the path pkexec ran,
never from an argument, so a caller of the passwordless path can't request
the unrestricted behaviour. It's a copy rather than a symlink so pkexec
matches the exact path. In both modes the helper also refuses a disk with
anything in its tree mounted at a system path (same list as
`core/safety.py`). `core/formatter.py:format_device` passes
`unrestricted=not device.removable`, using the same `RM` criterion, so the
normal flow never hits the password prompt and the override always does.
The source tree only has the removable-only name, so the unrestricted path
works only from the installed package.

Trade-off, accepted deliberately for UX: any process in the active local
session can wipe a removable device without a password. Inactive/remote
sessions (`allow_inactive`, `allow_any`) stay `no`.

### Why mounting the new partition uses `udisksctl` (subprocess) instead of the `gi.repository.UDisks` client

The GI `UDisks.Client` builds a cached `GDBusObjectManagerClient` at
construction time; picking up objects created *after* that (like the
partition `stamp-helper` just created) relies on the object-added D-Bus
signal being processed, which needs a GLib main loop iterating. The CLI
entry point doesn't run one. `udisksctl` (udisks2's own CLI, talking to the
same daemon, still no root/pkexec) sidesteps that timing dependency
entirely, so it's used for this one step in both the CLI and GUI code paths
— one code path beats two, and the GUI's real GLib main loop wouldn't have
actually made the GI-client route meaningfully better here anyway.

## `core/` vs `gui/` boundary

`core/` (`device_scanner.py`, `safety.py`, `formatter.py`, `content_writer.py`,
`logging_setup.py`, `models.py`) imports nothing from GTK/GObject-GUI
namespaces — only plain dataclasses, `subprocess`, and (in `formatter.py`)
`gi.repository.UDisks`/`GLib`, which are D-Bus bindings, not GUI toolkit
bindings. `gui/` and `cli/` both call into the same `core/` functions with
no duplicated logic. If a Qt or web GUI is ever built for another platform,
only `gui/` needs replacing — this was validated by writing `cli/main.py`
as effectively a second, independent consumer of the exact same core
functions the GUI uses, and it required zero changes to `core/`.

## macOS app (`macos/`)

A separate native SwiftUI app, not a port of the Python code: bundling
Python + GTK into a signed, notarized `.app` would be far heavier than the
logic it would reuse. `StampCore` mirrors `core/` module for module and has
no SwiftUI/AppKit imports:

| `core/` (Python) | `StampCore` (Swift) | macOS mechanism |
|---|---|---|
| `device_scanner.py` | `DiskScanner.swift` | `diskutil list -plist` + `diskutil info -plist` |
| `safety.py` | `Safety.swift` | same rules, macOS system mount points |
| `formatter.py` + `stamp-helper` | `Formatter.swift` | `diskutil eraseDisk FAT32 … MBRFormat`, `diskutil mount/eject` |
| `content_writer.py` | `ContentWriter.swift` | `/usr/bin/unzip`, data-only `copyfile` |
| `gui/main_window.py` worker | `StampJob.swift` | whole flow, used by the app and the tests |

**No privileged helper.** diskutil's FAT32 personality is MS-DOS FAT32
(`newfs_msdos -F 32`) at any size, so the reason the Linux build needs
`mkfs.vfat -F 32` through pkexec doesn't apply. diskutil erases external
disks for the console user without an admin password. Nothing runs as root.

**Boot disk detection.** `/` is a sealed APFS snapshot on a synthesized
container disk, not on the physical SSD. `DiskScanner` follows each APFS
container's `APFSPhysicalStores` back to the physical whole disk and
attributes the container's volume and snapshot mount points to it, so the
internal SSD carries `/` and `/System/Volumes/*` and `Safety` blocks it the
same way `core/safety.py` blocks the Linux root disk. Disks hosting an
`OSInternal` container are blocked too. Disk images (`VirtualOrPhysical =
Virtual` or `BusProtocol = Disk Image`) are dropped before any of that,
because they report `RemovableMedia` too.

**Removable.** `Disk.removable` is diskutil's `RemovableMedia`, the analogue
of lsblk `RM`. External SSDs/HDDs report fixed media, so like on Linux they
need the explicit override checkbox.

**Flow.** check zip → extract → re-scan and re-check the disk (refusing if
its size or model changed since the user confirmed) → erase → mount →
copy → remove macOS metadata → `sync` → verify top-level entries → eject.
Extraction happens before the erase, unlike the Linux worker.

**Copy.** `/usr/bin/rsync` is openrsync on current macOS and doesn't support
progress output, so the copy is done in Swift with
`copyfile(COPYFILE_DATA)`: data only, which also keeps macOS from writing
AppleDouble `._` files onto the FAT volume. Directory walking uses
`readdir`/`lstat`, because Foundation's `contentsOfDirectory` and
enumerators hide `._` files. With Foundation, `._` files inside a zip would
silently not be copied, and the metadata cleanup would find nothing to
remove.

**Device changes.** `DiskWatcher` subscribes to DiskArbitration
appeared/disappeared/description-changed callbacks and triggers a debounced
rescan. There is no polling.

**Distribution.** `macos/scripts/build-app.sh` builds a universal binary,
assembles `Stamp.app` (version taken from `packaging/nfpm.yaml`), signs
it with the hardened runtime, wraps it in a DMG, and notarizes and staples
when given a Developer ID identity and a notarytool profile. The app is not
sandboxed: App Sandbox doesn't allow running `diskutil` against raw disks.

## Testing reality check

- `core/device_scanner.py`, `core/safety.py`, `core/content_writer.py`,
  `core/logging_setup.py`, `core/formatter.py`'s pure logic, and
  `cli/main.py`'s command handlers: unit tested (87 tests, `tests/`), run on
  every change during development. `content_writer.py`'s `rsync`/`sync`
  calls are exercised for real (not mocked) against temp directories.
- `gui/*.py`: not unit tested by the pytest suite (no `gi` in the dev venv
  used for `tests/`), but constructed and driven directly against a real
  GTK4 4.22 / libadwaita 1.9 runtime (installed via Homebrew for this
  development session) — `DevicePicker` device list rendering and selection
  signals, zip loading and format-button gating, `ConfirmDialog`'s concise
  summary, the non-removable risk acknowledgement, and the
  full background-worker call order (`format → extract → mount → copy →
  sync → unmount`, with mocked `core` calls) were all exercised directly and
  behaved as intended. What's *not* verified: real GNOME window-manager
  chrome/theming, and anything involving actual udisks2/PolicyKit D-Bus
  calls or `pkexec`, since those only exist on Linux.
- `helper/stamp-helper`: `bash -n` syntax-checked; the actual `parted`/
  `mkfs.vfat`/`partprobe` sequence is unverified beyond that — it mirrors
  the prototype script's validated command sequence (spec §7) but should be
  run once against a real disposable card before trusting it in anger.
- `packaging/build-packages.sh`: CI builds the `.deb` and `.rpm`, installs
  each in Ubuntu, Debian and Fedora containers (and the AUR PKGBUILD in an
  Arch container), and
  checks that the GTK/libadwaita/UDisks typelibs and the external tools
  resolve. Containers have no polkit daemon or block devices, so the
  pkexec/polkit path itself is only verified on real machines.
