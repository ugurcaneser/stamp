"""Enumerate candidate removable block devices.

Primary data source is `lsblk -J` (JSON output), which is what udisks2 itself
ultimately reflects for topology/removable/mount info and is trivial to mock
in tests (see tests/test_device_scanner.py). Live formatting/mounting
operations (core/formatter.py) go through udisks2 D-Bus / the pkexec helper —
this module is read-only and never touches a device.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable

from core.models import Device, Partition

LSBLK_COLUMNS = "NAME,PATH,SIZE,MODEL,VENDOR,RM,RO,TYPE,MOUNTPOINTS,FSTYPE,LABEL,TRAN"

LsblkRunner = Callable[[], str]


class DeviceScanError(RuntimeError):
    """Raised when the underlying lsblk invocation fails or returns unparsable data."""


def _default_lsblk_runner() -> str:
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise DeviceScanError("lsblk is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeviceScanError("lsblk timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise DeviceScanError(f"lsblk failed: {exc.stderr.strip() if exc.stderr else exc}") from exc
    return result.stdout


def _to_int(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip() in ("1", "true", "True")


def _mountpoints_of(node: dict) -> list[str]:
    # lsblk exposes either "mountpoint" (singular, older) or "mountpoints" (list, newer).
    if "mountpoints" in node and node["mountpoints"] is not None:
        return [m for m in node["mountpoints"] if m]
    mp = node.get("mountpoint")
    return [mp] if mp else []


def parse_lsblk_json(raw: str) -> list[Device]:
    """Parse `lsblk -J -o <LSBLK_COLUMNS>` output into a list of whole-disk Devices."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeviceScanError(f"could not parse lsblk output: {exc}") from exc

    devices: list[Device] = []
    for node in data.get("blockdevices", []):
        if node.get("type") != "disk":
            continue

        partitions: list[Partition] = []
        for child in node.get("children", []) or []:
            if child.get("type") not in ("part", "crypt"):
                continue
            partitions.append(
                Partition(
                    path=child.get("path") or f"/dev/{child.get('name')}",
                    name=child.get("name", ""),
                    size_bytes=_to_int(child.get("size")),
                    fstype=child.get("fstype"),
                    mountpoints=_mountpoints_of(child),
                    label=child.get("label"),
                )
            )

        devices.append(
            Device(
                path=node.get("path") or f"/dev/{node.get('name')}",
                name=node.get("name", ""),
                size_bytes=_to_int(node.get("size")),
                model=node.get("model"),
                vendor=node.get("vendor"),
                removable=_to_bool(node.get("rm")),
                read_only=_to_bool(node.get("ro")),
                transport=node.get("tran"),
                partitions=partitions,
            )
        )
    return devices


def scan_devices(lsblk_runner: LsblkRunner = _default_lsblk_runner) -> list[Device]:
    """Return every whole-disk block device lsblk knows about (no filtering applied)."""
    raw = lsblk_runner()
    return parse_lsblk_json(raw)


class DevicePoller:
    """Polls scan_devices() on a background thread and notifies on changes.

    A udev-event-driven watcher (e.g. via GUdev/pyudev) would be more elegant
    and is a reasonable future swap, but a poll loop is simple, dependency-free,
    and satisfies "auto-refresh ... or at minimum a manual refresh button".
    """

    def __init__(
        self,
        on_change: Callable[[list[Device]], None],
        on_error: Callable[[Exception], None] | None = None,
        interval_seconds: float = 2.0,
        lsblk_runner: LsblkRunner = _default_lsblk_runner,
    ) -> None:
        self._on_change = on_change
        self._on_error = on_error
        self._interval = interval_seconds
        self._lsblk_runner = lsblk_runner
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_signature: tuple | None = None

    @staticmethod
    def _signature(devices: list[Device]) -> tuple:
        return tuple(
            (d.path, d.size_bytes, d.removable, tuple(p.path for p in d.partitions), tuple(p.mountpoints[:1] for p in d.partitions))
            for d in devices
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                devices = scan_devices(self._lsblk_runner)
                sig = self._signature(devices)
                if sig != self._last_signature:
                    self._last_signature = sig
                    self._on_change(devices)
            except Exception as exc:  # noqa: BLE001 - surface to caller, keep polling
                if self._on_error:
                    self._on_error(exc)
            self._stop_event.wait(self._interval)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="stamp-device-poller")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
