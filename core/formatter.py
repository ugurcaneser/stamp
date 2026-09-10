"""Partition + force-FAT32 logic.

PRIVILEGE APPROACH ACTUALLY USED (see ARCHITECTURE.md for the full rationale):
Hybrid, as pre-authorized by the spec's §3 fallback clause:

  - unmounting a device's pre-existing partitions (before formatting) :
    udisks2 D-Bus (Filesystem.Unmount) via `gi.repository.UDisks`, called as
    the logged-in user; PolicyKit prompts the user in-session. No pkexec, no
    custom polkit policy needed for this part.

  - mounting/unmounting the freshly-created partition (after formatting) :
    `udisksctl` (udisks2's own CLI, still no pkexec/root) rather than the GI
    client — see mount_partition()'s docstring for why.

  - partition table + mkfs.vfat -F 32 : the small privileged helper at
    helper/stamp-helper, invoked via `pkexec`. Reason this couldn't stay on
    the pure-udisks2 path: udisks2's Block.Format() D-Bus method (the call
    that would create a partition table / filesystem for us) does not expose
    an option to force the FAT bit width — its options dict only supports
    things like "label" and "take-ownership", not dosfstools' `-F 32`. Since
    forcing FAT32 "regardless of card size" via `-F 32` is this app's entire
    reason to exist (§1, §4.3), leaving that decision to mkfs.fat's own
    size-based auto-detection is not acceptable — it must be explicit and
    guaranteed. The helper is intentionally tiny and auditable: it accepts
    exactly one whitelisted subcommand, validates the device path against
    /dev/<letters><digits> / /dev/mmcblk<N> / /dev/nvme<N>n<N> shapes, and
    runs parted + mkfs.vfat -F 32 with no shell interpolation of user input.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.models import Device
from core.safety import SafetyError, evaluate_device_safety

log = logging.getLogger("stamp")

DEFAULT_LABEL = "STAMP"

UnmountFn = Callable[[Device], None]
HelperInvoker = Callable[[str, str, "ProgressReporter | None"], None]
ProgressReporter = Callable[[str], None]


class FormatError(RuntimeError):
    """Raised for any failure while unmounting, partitioning, or formatting."""


@dataclass
class FormatResult:
    device_path: str
    partition_path: str
    label: str


# --- udisks2-backed unmount ----------------------------------------------------


def default_unmount_all(device: Device) -> None:
    """Unmount every currently-mounted partition of `device` via udisks2 D-Bus."""
    try:
        import gi

        gi.require_version("UDisks", "2.0")
        from gi.repository import GLib, UDisks
    except (ImportError, ValueError) as exc:
        raise FormatError(
            "udisks2 GObject-introspection bindings are not available "
            "(install gir1.2-udisks2 on the target Ubuntu machine)"
        ) from exc

    client = UDisks.Client.new_sync(None)
    manager = client.get_object_manager()

    for partition in device.partitions:
        if not partition.is_mounted:
            continue
        obj = _find_block_object(manager, partition.path)
        if obj is None:
            raise FormatError(f"udisks2 does not know about {partition.path}")
        fs = obj.get_filesystem()
        if fs is None:
            raise FormatError(f"{partition.path} has no udisks2 Filesystem interface to unmount")
        try:
            fs.call_unmount_sync(GLib.Variant("a{sv}", {}), None)
        except GLib.Error as exc:
            raise FormatError(
                f"failed to unmount {partition.path}: {exc.message}. "
                "It may be busy (a file open, a shell cd'd into it, etc)."
            ) from exc


def _find_block_object(manager, device_path: str):
    for obj in manager.get_objects():
        block = obj.get_block()
        if block is None:
            continue
        dev = block.props.device
        # Some bindings versions expose the device path as a NUL-terminated bytes value.
        if isinstance(dev, bytes):
            dev = dev.decode("utf-8").rstrip("\x00")
        if dev == device_path:
            return obj
    return None


# --- pkexec-backed helper invocation for partition table + mkfs.vfat -F 32 -----

_HELPER_RELATIVE_PATH = os.path.join("helper", "stamp-helper")
_INSTALLED_HELPER_PATH = "/usr/lib/stamp/stamp-helper"

# Matches /dev/sdX, /dev/mmcblkN, /dev/nvmeXnY — the device *itself*, not a
# partition (no trailing partition number/suffix). Mirrors the validation the
# helper script re-does independently before touching anything.
_DEVICE_PATH_RE = re.compile(r"^/dev/(sd[a-z]+|mmcblk\d+|nvme\d+n\d+)$")


def _resolve_helper_path() -> str:
    if os.path.exists(_INSTALLED_HELPER_PATH):
        return _INSTALLED_HELPER_PATH
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dev_path = os.path.join(project_root, _HELPER_RELATIVE_PATH)
    if os.path.exists(dev_path):
        return dev_path
    raise FormatError(
        "could not locate the stamp-helper privileged helper script "
        f"(looked in {_INSTALLED_HELPER_PATH} and {dev_path})"
    )


def sanitize_label(label: str | None) -> str:
    """FAT32 volume labels: up to 11 chars, conventionally uppercase, limited charset."""
    label = (label or DEFAULT_LABEL).strip() or DEFAULT_LABEL
    cleaned = "".join(c for c in label if c.isalnum() or c in "-_ ").strip()
    cleaned = cleaned[:11] or DEFAULT_LABEL
    return cleaned.upper()


def partition_path_for(device_path: str, index: int = 1) -> str:
    """Compute the expected partition device path for a single-partition disk.

    /dev/sdb -> /dev/sdb1 ; /dev/mmcblk0 -> /dev/mmcblk0p1 ; /dev/nvme0n1 -> /dev/nvme0n1p1
    """
    if re.search(r"\d$", device_path):
        return f"{device_path}p{index}"
    return f"{device_path}{index}"


def default_helper_invoke(device_path: str, label: str, progress: ProgressReporter | None = None) -> None:
    if not _DEVICE_PATH_RE.match(device_path):
        raise FormatError(f"refusing to format {device_path}: does not look like a whole-disk device path")

    helper_path = _resolve_helper_path()
    safe_label = sanitize_label(label)
    cmd = ["pkexec", helper_path, "format-fat32", device_path, safe_label]

    if progress:
        progress(f"Requesting privileged access to partition and format {device_path}...")

    log.info("running privileged command: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except FileNotFoundError as exc:
        log.error("pkexec not found while formatting %s", device_path)
        raise FormatError("pkexec is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        log.error("formatting %s timed out", device_path)
        raise FormatError(f"formatting {device_path} timed out after 15 minutes") from exc

    if result.returncode == 126 or result.returncode == 127:
        log.error("pkexec authentication cancelled/denied for %s", device_path)
        raise FormatError("authentication was cancelled or denied (pkexec)")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        log.error("stamp-helper failed for %s (exit %s): %s", device_path, result.returncode, detail)
        raise FormatError(f"formatting {device_path} failed: {detail or f'exit code {result.returncode}'}")
    log.info("stamp-helper succeeded for %s: %s", device_path, (result.stdout or "").strip())


# --- mounting the newly-created partition (udisksctl, not pkexec) ---------------
#
# This uses udisksctl (udisks2's own CLI, still no pkexec/root) rather than the
# GI UDisks.Client here: the partition was just created by stamp-helper a moment
# ago and won't reliably show up in an already-constructed Client's cached
# object manager without pumping a GLib main loop, which the CLI entry point
# doesn't run. udisksctl talks to the same udisks2 daemon over D-Bus and
# sidesteps that timing issue entirely. The GUI, which does run a GLib main
# loop, could use either; using udisksctl here too keeps one code path.

CommandRunner = Callable[[list], subprocess.CompletedProcess]
PathExistsFn = Callable[[str], bool]

_MOUNT_PATH_RE = re.compile(r" at (/\S+)")


def _default_command_runner(cmd: list, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def wait_for_device_node(
    path: str,
    attempts: int = 20,
    delay_seconds: float = 0.25,
    exists_fn: PathExistsFn = os.path.exists,
) -> None:
    for _ in range(attempts):
        if exists_fn(path):
            return
        time.sleep(delay_seconds)
    raise FormatError(f"{path} never appeared (partition creation may have failed)")


def mount_partition(
    partition_path: str,
    progress: ProgressReporter | None = None,
    runner: CommandRunner = _default_command_runner,
    exists_fn: PathExistsFn = os.path.exists,
) -> str:
    """Mount partition_path via `udisksctl mount`, returning the mount path."""
    if progress:
        progress(f"Mounting {partition_path}...")
    wait_for_device_node(partition_path, exists_fn=exists_fn)

    try:
        result = runner(["udisksctl", "mount", "-b", partition_path, "--no-user-interaction"])
    except FileNotFoundError as exc:
        raise FormatError("udisksctl is not installed") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise FormatError(f"failed to mount {partition_path}: {detail}")

    match = _MOUNT_PATH_RE.search(result.stdout or "")
    if not match:
        raise FormatError(f"could not parse mount path from udisksctl output: {(result.stdout or '').strip()}")
    mount_path = match.group(1)
    log.info("mounted %s at %s", partition_path, mount_path)
    return mount_path


def unmount_partition(
    partition_path: str,
    progress: ProgressReporter | None = None,
    runner: CommandRunner = _default_command_runner,
) -> None:
    if progress:
        progress(f"Unmounting {partition_path}...")
    try:
        result = runner(["udisksctl", "unmount", "-b", partition_path, "--no-user-interaction"])
    except FileNotFoundError as exc:
        raise FormatError("udisksctl is not installed") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise FormatError(f"failed to unmount {partition_path}: {detail}")
    log.info("unmounted %s", partition_path)


# --- orchestration ---------------------------------------------------------------


def format_device(
    device: Device,
    root_disk_path: str,
    label: str = DEFAULT_LABEL,
    allow_non_removable_override: bool = False,
    unmount_fn: UnmountFn = default_unmount_all,
    helper_invoke: HelperInvoker = default_helper_invoke,
    progress_cb: ProgressReporter | None = None,
) -> FormatResult:
    """Unmount, then partition + force-FAT32 `device`. Re-validates safety itself —
    never trust a caller's earlier check, since time may have passed (§4.5)."""

    log.info(
        "format_device requested: device=%s size=%s model=%r label=%r override=%s",
        device.path, device.size_bytes, device.display_model, label, allow_non_removable_override,
    )
    check = evaluate_device_safety(device, root_disk_path, allow_non_removable_override)
    if not check.ok:
        log.warning("refusing to format %s: %s", device.path, "; ".join(check.blocking_reasons))
        raise SafetyError("; ".join(check.blocking_reasons))

    def report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    if device.has_mounted_partitions:
        report(f"Unmounting {device.path}'s mounted partitions...")
        unmount_fn(device)

    report(f"Creating a fresh partition table on {device.path} and formatting as FAT32...")
    safe_label = sanitize_label(label)
    helper_invoke(device.path, safe_label, progress_cb)

    partition_path = partition_path_for(device.path)
    report(f"Format complete: {partition_path} is FAT32, labeled {safe_label}.")
    return FormatResult(device_path=device.path, partition_path=partition_path, label=safe_label)
