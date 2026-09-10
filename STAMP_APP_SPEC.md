# Stamp — App Specification

## 1. Purpose

Build a Linux desktop application called **Stamp** (with an optional CLI
mode) that prepares a
micro SD card for use:

1. Formats the target SD card as **FAT32**, explicitly supporting **large
   cards (>32GB)** — most stock GUI formatters (GParted's default dialog,
   Windows' format tool, etc.) silently refuse or cap FAT32 above 32GB. This
   app must force FAT32 regardless of card size.
2. Takes a **zip file** the user provides, extracts it, and copies its
   contents onto the freshly formatted card.
3. Does this safely: SD card formatting is destructive, and the #1 failure
   mode of tools like this is wiping the wrong disk. Safety UX is a first-class
   requirement, not an afterthought.

A working bash-script prototype already exists (`prepare-sdcard.sh`) and
proves out the underlying commands (`parted`, `mkfs.vfat -F 32`, `unzip`,
`rsync`). This spec is for turning that logic into a proper packaged app with
a GUI, better safety guarantees, and privilege separation.

## 2. Target platform

- **Primary target: Ubuntu (22.04+ / 24.04+), GNOME desktop.** Optimize for
  this specifically — no need to hedge for other distros or desktop
  environments right now.
- Multi-platform (other distros, possibly Windows/macOS) is a **future**
  consideration, not a current requirement. Don't add abstraction layers
  purely to hedge for that — but see §3 for the one architectural boundary
  worth keeping clean so that future work isn't a full rewrite.
- Target x86_64 dev/desktop machines. (Running the GUI itself on a headless
  Raspberry Pi is out of scope — this tool runs on the Ubuntu machine
  preparing a card *for* an embedded device, not on the device itself.)

## 3. Recommended tech stack (Ubuntu-optimized)

- **Language:** Python 3.
- **GUI toolkit:** **GTK4 + libadwaita**, via PyGObject (`gi.repository`).
  Reasoning: this is Ubuntu/GNOME's native toolkit — correct window
  decorations, correct light/dark theme handling, correct file picker
  (`Gtk.FileDialog`) behavior, and it's what a polished GNOME-era Ubuntu app
  is expected to look like out of the box. No custom theming work needed.
- **Privileged operations:** Do **not** require the whole GUI app to run as
  root. Instead:
  - Run the GUI as a normal user.
  - Use the **udisks2 D-Bus API** as the primary mechanism for
    unmounting/mounting and, where udisks2's `Format`/`Partition` D-Bus calls
    support it, for partitioning and formatting too. On Ubuntu, udisks2 is
    already installed and integrated with PolicyKit, so this is the natural
    fit — no custom polkit policy file needed for the parts udisks2 already
    covers.
  - Access udisks2 via **PyGObject's GObject introspection bindings**
    (`gi.repository.UDisks`) so it's the same binding ecosystem as the GTK
    GUI — avoids mixing in a separate D-Bus library.
  - If forcing `-F 32` FAT32 on very large cards isn't achievable through
    udisks2's format call parameters, fall back to a minimal privileged
    helper script invoked via **`pkexec`** just for the `mkfs.vfat -F 32`
    step, with everything else (unmount, partitioning, mount, copy) staying
    on the udisks2/user-space path. Keep this fallback helper as small and
    auditable as possible — it should do nothing but run one whitelisted
    command with validated arguments.
- **Packaging:** Ship as a **`.deb` package** named `stamp` — this is the
  Ubuntu-native distribution format, integrates with `apt`, and gives a normal
  `.desktop` entry / app-menu presence. (Skip Snap: Snap's confinement model
  makes raw block-device access and udisks2/polkit interaction meaningfully
  harder to get right, and it doesn't buy anything for an Ubuntu-only tool.)

**Keeping the door open for multiplatform later:** the only thing worth
doing now for that future goal is keeping `core/` (device scanning,
formatting, copy logic) free of any GTK imports — it should only know about
udisks2/D-Bus and shell commands. That way, if a Qt or web-based GUI is
built later for other platforms, only the `gui/` layer needs replacing.
Don't build a plugin system or abstract GUI interface for this now — just
don't leak GTK types into `core/`.

State clearly in the implementation which privilege approach was actually
used (pure udisks2 vs. udisks2 + pkexec fallback), since this affects the
security review of the app.

## 4. Functional requirements

### 4.1 Device selection
- List candidate removable block devices (query via `lsblk`/`udisks2`, not
  just `/dev/sd*` guessing).
- Show for each: device path, size, model/vendor string, whether it's
  removable, current partition/filesystem info if any.
- Auto-refresh the list when a device is inserted/removed (listen for udev
  events, or at minimum a manual refresh button).
- Never list, and actively filter out, the disk currently mounted as `/`
  (root filesystem) or containing the running OS.

### 4.2 Zip file selection
- Standard file picker, filtered to `.zip`.
- Validate the zip is readable and non-empty before allowing the user to
  proceed (fail fast with a clear error, not mid-write).
- Show a preview: file count, total uncompressed size, and warn if
  uncompressed size exceeds the target card's free capacity.

### 4.3 Formatting
- Always create a fresh MBR (or GPT — pick one and be consistent) partition
  table with a single partition spanning the disk.
- Force FAT32 via `mkfs.vfat -F 32` (or udisks2 equivalent) regardless of
  card size — this is the core value proposition over stock tools.
- Allow the user to set a volume label (optional text field, sane default).

### 4.4 Copy contents
- Extract the zip to a temp location, then copy (rsync-style, preserving
  directory structure) onto the newly formatted partition.
- Show real progress: extraction progress and copy progress as two visible
  phases, not a single indeterminate spinner.
- `sync` and cleanly unmount at the end; tell the user explicitly when it's
  safe to physically remove the card.

### 4.5 Safety requirements (critical — do not relax these)
- **Two-step confirmation** before any destructive action: e.g. a summary
  screen listing device path/size/model + zip file, requiring the user to
  type the device path (or check an explicit "I understand this erases
  everything on this device" box) before the format button becomes active.
- Refuse to operate on:
  - The device backing the root filesystem.
  - Any device that isn't flagged removable, unless the user explicitly
    overrides with a second confirmation acknowledging the risk.
  - Any device that currently has mounted partitions the app can't safely
    unmount.
- Make the "cancel" path safe at every stage before the actual format/write
  step begins (i.e. no destructive action happens until the user hits a
  clearly-labeled final "Format & Copy" button).
- Log every operation (device, timestamps, commands run) to a local log file
  for post-hoc debugging if something goes wrong.

### 4.6 Error handling
- Any failure (unmount fails, format fails, zip is corrupt, copy is
  interrupted, card is removed mid-write) must produce a clear, specific
  error message — not a raw stack trace or exit code.
- Where possible, verify the write afterward (e.g. remount read-only and spot
  check that expected top-level entries exist).

## 5. Non-functional requirements

- The GUI must remain responsive during long operations (formatting large
  cards and copying large zips can take minutes) — use background
  threads/async, never block the main event loop.
- Provide a `--cli` mode that exposes the same functionality without a GUI,
  for scripting/automation, reusing the same core logic (don't duplicate the
  device/format/copy logic between GUI and CLI — share one core module).
- No telemetry, no network calls required for core functionality.

## 6. Suggested project structure

```
stamp/
├── core/                       # no GTK imports here — keep GUI-agnostic
│   ├── device_scanner.py       # enumerate & describe candidate devices (udisks2)
│   ├── formatter.py            # partition + mkfs.vfat -F 32 logic (udisks2, +pkexec fallback)
│   ├── content_writer.py       # zip extraction + copy logic
│   └── safety.py               # root-disk detection, confirmation helpers
├── gui/                        # GTK4 + libadwaita
│   ├── main_window.py
│   ├── device_picker.py
│   ├── confirm_dialog.py
│   ├── progress_view.py
│   └── ui/                     # .ui XML (Blueprint or Cambalache-authored)
├── cli/
│   └── main.py
├── helper/                     # privileged fallback helper invoked via pkexec, only if udisks2 can't force FAT32
│   └── stamp-helper
├── packaging/
│   ├── debian/                 # control, rules, postinst, etc. for the .deb build
│   ├── stamp.desktop
│   ├── org.example.stamp.policy   # polkit policy, only if pkexec fallback is used
│   └── build-deb.sh
├── tests/
└── README.md
```

## 7. Reference: known-good shell commands

These were validated in the prototype script and should inform the
implementation (adapt to udisks2 D-Bus calls if that approach is chosen):

```bash
parted -s "$DEVICE" mklabel msdos
parted -s "$DEVICE" mkpart primary fat32 1MiB 100%
parted -s "$DEVICE" set 1 lba on
partprobe "$DEVICE"
mkfs.vfat -F 32 -n "$LABEL" "$PARTITION"
```

## 8. Out of scope (for now)

- Non-Ubuntu distros and non-GNOME desktops — revisit in a future
  multi-platform pass (at which point the `core/` vs `gui/` split from §3
  pays off).
- Windows/macOS support.
- exFAT or NTFS output (FAT32 only, by design — this is the whole point of
  the tool).
- Multi-zip / multi-source merging in one run.
- Verifying zip contents against any particular expected structure (e.g. no
  built-in assumptions about this being for a specific embedded device's
  content layout — keep the tool generic).

## 9. Suggested first prompt for Claude Code

> Read this spec (`STAMP_APP_SPEC.md`) in full. Start by scaffolding
> the `core/` module with `device_scanner.py` and `safety.py`, including
> unit tests that mock `lsblk`/`udisks2` output — get device enumeration and
> root-disk exclusion logic solid and tested before touching any GUI code or
> any actual formatting logic. Confirm the privilege-separation approach
> (udisks2 D-Bus vs pkexec helper) before implementing `formatter.py`, since
> that decision affects the whole architecture.
