# Architecture

## Privilege separation (the decision the spec asked to be stated explicitly)

**Hybrid: udisks2 D-Bus for almost everything, a tiny `pkexec` helper for
exactly one step.**

| Operation | Mechanism | Runs as |
|---|---|---|
| Enumerate devices | `lsblk -J` (subprocess) | user |
| Determine root disk | `findmnt` + `lsblk -no PKNAME` (subprocess) | user |
| Unmount device's existing partitions before formatting | udisks2 D-Bus, `Filesystem.Unmount` via `gi.repository.UDisks` (`core/formatter.py:default_unmount_all`) | user (PolicyKit prompts in-session) |
| Create MBR partition table + one partition + `mkfs.vfat -F 32` | **`helper/stamp-helper` via `pkexec`** | root |
| Mount the freshly-formatted partition | `udisksctl mount -b` (subprocess) | user (PolicyKit prompts in-session) |
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

`packaging/org.example.stamp.policy` ties a custom PolicyKit action to this
helper's exact installed path (`/usr/lib/stamp/stamp-helper`) via the
`org.freedesktop.policykit.exec.path` annotation, so the auth prompt shows a
specific "partition and format the selected removable device as FAT32"
message instead of the generic "run a program as root" pkexec prompt.

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

## Testing reality check

- `core/device_scanner.py`, `core/safety.py`, `core/content_writer.py`,
  `core/logging_setup.py`, `core/formatter.py`'s pure logic, and
  `cli/main.py`'s command handlers: unit tested (84 tests, `tests/`), run on
  every change during development. `content_writer.py`'s `rsync`/`sync`
  calls are exercised for real (not mocked) against temp directories.
- `gui/*.py`: not unit tested by the pytest suite (no `gi` in the dev venv
  used for `tests/`), but constructed and driven directly against a real
  GTK4 4.22 / libadwaita 1.9 runtime (installed via Homebrew for this
  development session) — `DevicePicker` device list rendering and selection
  signals, zip loading and format-button gating, `ConfirmDialog`'s
  checkbox+text-match gating, the non-removable override dialog, and the
  full background-worker call order (`format → extract → mount → copy →
  sync → unmount`, with mocked `core` calls) were all exercised directly and
  behaved as intended. What's *not* verified: real GNOME window-manager
  chrome/theming, and anything involving actual udisks2/PolicyKit D-Bus
  calls or `pkexec`, since those only exist on Linux.
- `helper/stamp-helper`: `bash -n` syntax-checked; the actual `parted`/
  `mkfs.vfat`/`partprobe` sequence is unverified beyond that — it mirrors
  the prototype script's validated command sequence (spec §7) but should be
  run once against a real disposable card before trusting it in anger.
- `packaging/build-deb.sh`: actually run end-to-end (via Homebrew's `dpkg`)
  to produce and inspect a real `.deb`; installing it was not verified since
  `dpkg -i`/`apt` don't function in this environment.
