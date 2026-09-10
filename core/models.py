"""Shared data types for core/. Plain dataclasses only — no GTK, no D-Bus types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Partition:
    path: str  # e.g. /dev/sdb1
    name: str  # e.g. sdb1
    size_bytes: int
    fstype: str | None
    mountpoints: list[str] = field(default_factory=list)
    label: str | None = None

    @property
    def is_mounted(self) -> bool:
        return len(self.mountpoints) > 0


@dataclass
class Device:
    """A candidate block device (whole disk), as reported by lsblk."""

    path: str  # e.g. /dev/sdb
    name: str  # e.g. sdb
    size_bytes: int
    model: str | None
    vendor: str | None
    removable: bool
    read_only: bool
    transport: str | None  # "usb", "sata", "mmc", "nvme", ...
    partitions: list[Partition] = field(default_factory=list)

    @property
    def size_human(self) -> str:
        return human_size(self.size_bytes)

    @property
    def display_model(self) -> str:
        parts = [p for p in (self.vendor, self.model) if p]
        return " ".join(parts).strip() or "Unknown device"

    @property
    def has_mounted_partitions(self) -> bool:
        return any(p.is_mounted for p in self.partitions)

    @property
    def all_mountpoints(self) -> list[str]:
        out: list[str] = []
        for p in self.partitions:
            out.extend(p.mountpoints)
        return out


def human_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable size (binary units, GB/TB style labels)."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"
