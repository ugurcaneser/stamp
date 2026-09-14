# Stamp

Formats a removable SD card or USB drive as **FAT32** — explicitly forcing
FAT32 on cards larger than 32GB, which most stock GUI formatters (GParted's
default dialog, Windows' Format tool) silently refuse or cap — then copies
the contents of a zip file you provide onto it.

Targets Ubuntu 22.04+/24.04+ on GNOME. GTK4 + libadwaita GUI, plus a `--cli`
mode for scripting. See `STAMP_APP_SPEC.md` for the full specification this
was built from, and `ARCHITECTURE.md` for the privilege-separation design.

## Install

```sh
./packaging/build-deb.sh          # writes dist/stamp_1.0.2_all.deb
sudo apt install ./dist/stamp_1.0.2_all.deb
```

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
- Formatting uses one concise summary dialog. The "Format & Copy" button stays
  disabled until the user acknowledges that the selected device will be erased.
- A device not flagged removable adds one explicit risk checkbox to that same
  dialog; it does not add another popup. Root/system disks remain blocked.
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
packages without a manually-set `PYTHONPATH`. GitHub Actions runs the suite on
Python 3.10 (Ubuntu 22.04's baseline) and Python 3.12 (Ubuntu 24.04's baseline),
checks Python and shell syntax, and verifies that the `.deb` can be built.

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

## What's deliberately not here

- Non-Ubuntu distros, Windows, macOS: out of scope per the spec (§8).
- `gui/ui/*.ui` Blueprint/Cambalache files: widgets are built
  programmatically in Python instead. This is a deliberate deviation from
  the spec's suggested layout — Blueprint files can't be compiled/validated
  without a Linux toolchain, and programmatic construction was something
  that could actually be built and driven against a real GTK4 runtime here.
- Full debhelper/dh-python packaging: `build-deb.sh` assembles the package
  tree and calls `dpkg-deb --build` directly. Simpler and fully verifiable
  locally; reasonable to upgrade to dh-python later if this is ever
  submitted to an actual Debian/Ubuntu archive.
