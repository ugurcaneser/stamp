"""Safety checks: root-disk detection, protected-mountpoint detection, confirmation helpers.

This module never performs a destructive action itself — it only answers
"is it safe to touch this device" and "did the user confirm correctly".
The actual refuse-to-operate enforcement lives in formatter.py, which must
call evaluate_device_safety() before doing anything destructive.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from core.models import Device

FindmntRunner = Callable[[], str]
PknameRunner = Callable[[str], str]

# Mountpoints (or prefixes) that mark a disk as "carries the running OS" even
# if it isn't the "/" disk itself (e.g. a separate /home or /boot/efi disk).
PROTECTED_MOUNTPOINT_PREFIXES = ("/", "/boot", "/home", "/var", "/usr", "/etc", "/opt", "/srv")
# "/" itself is a prefix of everything above, so match it exactly for the root
# case and prefix-match ("/boot", "/boot/") for the rest.
_ROOT_ONLY = "/"

CONFIRM_CHECKBOX_TEXT = "I understand this erases everything on this device"


class SafetyError(RuntimeError):
    """Raised by callers that ignore evaluate_device_safety() and try to proceed anyway."""


@dataclass
class SafetyCheckResult:
    ok: bool
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requires_override: bool = False  # e.g. non-removable device: allowed only with explicit override


def _default_findmnt_runner() -> str:
    result = subprocess.run(
        ["findmnt", "-no", "SOURCE", "/"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def _default_pkname_runner(partition_path: str) -> str:
    result = subprocess.run(
        ["lsblk", "-no", "PKNAME", partition_path],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def get_root_disk_path(
    findmnt_runner: FindmntRunner = _default_findmnt_runner,
    pkname_runner: PknameRunner = _default_pkname_runner,
) -> str:
    """Return the whole-disk device path backing the running system's "/" filesystem.

    Resolves through the partition (e.g. /dev/sda2) to its parent disk
    (/dev/sda) so the whole disk is excluded, not just the root partition.
    """
    source = findmnt_runner()
    if not source:
        raise SafetyError("could not determine the device backing '/' (findmnt returned nothing)")
    if not source.startswith("/dev/"):
        # e.g. an overlay/tmpfs root in a container — nothing to protect against here.
        return source
    pkname = pkname_runner(source)
    if not pkname:
        # source is already a whole disk (root directly on a disk, no partition layer)
        return source
    return f"/dev/{pkname}"


def is_root_disk(device: Device, root_disk_path: str) -> bool:
    return device.path == root_disk_path


def has_protected_mountpoint(device: Device) -> bool:
    for mp in device.all_mountpoints:
        if mp == _ROOT_ONLY:
            return True
        if any(mp == prefix or mp.startswith(prefix + "/") for prefix in PROTECTED_MOUNTPOINT_PREFIXES if prefix != "/"):
            return True
    return False


def evaluate_device_safety(
    device: Device,
    root_disk_path: str,
    allow_non_removable_override: bool = False,
) -> SafetyCheckResult:
    """Decide whether `device` may even be offered/attempted for format+write.

    Order matters: root-disk and protected-mountpoint checks are absolute and
    can never be overridden. The non-removable check *can* be overridden by
    the caller (after a second, explicit confirmation from the user).
    """
    blocking: list[str] = []
    warnings: list[str] = []
    requires_override = False

    if is_root_disk(device, root_disk_path):
        blocking.append(
            f"{device.path} is the disk backing the running operating system ('/') and cannot be selected."
        )

    if has_protected_mountpoint(device):
        blocking.append(
            f"{device.path} has a partition mounted at a system path (e.g. /, /boot, /home) and cannot be selected."
        )

    if not device.removable:
        if allow_non_removable_override:
            warnings.append(f"{device.path} is not flagged as removable. Proceeding because you explicitly overrode this check.")
        else:
            blocking.append(f"{device.path} is not flagged as removable.")
            requires_override = True

    if device.read_only:
        blocking.append(f"{device.path} is read-only and cannot be formatted.")

    return SafetyCheckResult(
        ok=len(blocking) == 0,
        blocking_reasons=blocking,
        warnings=warnings,
        requires_override=requires_override,
    )


def filter_candidate_devices(devices: list[Device], root_disk_path: str) -> list[Device]:
    """Devices fit to even *list* in the picker: never the root disk or a disk with
    a protected mountpoint. Non-removable devices are still listed (so the user can
    see and consciously override), just not pre-selected/highlighted as safe.
    """
    return [d for d in devices if not is_root_disk(d, root_disk_path) and not has_protected_mountpoint(d)]


def confirmation_text_matches(device_path: str, typed: str) -> bool:
    return typed.strip() == device_path


def checkbox_confirms(checked: bool) -> bool:
    return checked is True
