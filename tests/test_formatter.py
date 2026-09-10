import subprocess

import pytest

from core.formatter import (
    FormatError,
    default_helper_invoke,
    format_device,
    mount_partition,
    partition_path_for,
    sanitize_label,
    unmount_partition,
    wait_for_device_node,
)
from core.models import Device, Partition
from core.safety import SafetyError


def make_device(path, removable=True, read_only=False, mountpoints=None):
    parts = []
    if mountpoints:
        parts.append(
            Partition(path=f"{path}1", name=f"{path.split('/')[-1]}1", size_bytes=1000, fstype="exfat", mountpoints=mountpoints)
        )
    return Device(
        path=path,
        name=path.split("/")[-1],
        size_bytes=64_000_000_000,
        model="Test",
        vendor="Test",
        removable=removable,
        read_only=read_only,
        transport="usb",
        partitions=parts,
    )


# --- partition_path_for ----------------------------------------------------------


def test_partition_path_for_sd_style():
    assert partition_path_for("/dev/sdb") == "/dev/sdb1"


def test_partition_path_for_mmcblk_style():
    assert partition_path_for("/dev/mmcblk0") == "/dev/mmcblk0p1"


def test_partition_path_for_nvme_style():
    assert partition_path_for("/dev/nvme0n1") == "/dev/nvme0n1p1"


# --- sanitize_label ----------------------------------------------------------------


def test_sanitize_label_default_when_empty():
    assert sanitize_label("") == "STAMP"
    assert sanitize_label(None) == "STAMP"


def test_sanitize_label_truncates_and_uppercases():
    assert sanitize_label("my really long label") == "MY REALLY L"


def test_sanitize_label_strips_illegal_characters():
    assert sanitize_label("bad/label:name") == "BADLABELNAM"


# --- format_device orchestration ----------------------------------------------------


def test_format_device_refuses_root_disk():
    dev = make_device("/dev/nvme0n1", mountpoints=["/"])
    with pytest.raises(SafetyError):
        format_device(
            dev,
            root_disk_path="/dev/nvme0n1",
            unmount_fn=lambda d: None,
            helper_invoke=lambda *a: None,
        )


def test_format_device_skips_unmount_when_nothing_mounted():
    dev = make_device("/dev/sdb")
    calls = {"unmount": 0, "helper": []}

    def fake_unmount(d):
        calls["unmount"] += 1

    def fake_helper(path, label, progress):
        calls["helper"].append((path, label))

    result = format_device(
        dev,
        root_disk_path="/dev/nvme0n1",
        label="MYCARD",
        unmount_fn=fake_unmount,
        helper_invoke=fake_helper,
    )
    assert calls["unmount"] == 0
    assert calls["helper"] == [("/dev/sdb", "MYCARD")]
    assert result.partition_path == "/dev/sdb1"
    assert result.label == "MYCARD"


def test_format_device_unmounts_when_mounted():
    dev = make_device("/dev/sdb", mountpoints=["/media/user/SDCARD"])
    calls = {"unmount": 0}

    def fake_unmount(d):
        calls["unmount"] += 1

    format_device(
        dev,
        root_disk_path="/dev/nvme0n1",
        unmount_fn=fake_unmount,
        helper_invoke=lambda *a: None,
    )
    assert calls["unmount"] == 1


def test_format_device_calls_unmount_before_helper():
    dev = make_device("/dev/sdb", mountpoints=["/media/user/SDCARD"])
    order = []

    format_device(
        dev,
        root_disk_path="/dev/nvme0n1",
        unmount_fn=lambda d: order.append("unmount"),
        helper_invoke=lambda *a: order.append("helper"),
    )
    assert order == ["unmount", "helper"]


def test_format_device_propagates_unmount_failure_without_calling_helper():
    dev = make_device("/dev/sdb", mountpoints=["/media/user/SDCARD"])
    calls = {"helper": 0}

    def failing_unmount(d):
        raise FormatError("busy")

    def fake_helper(*a):
        calls["helper"] += 1

    with pytest.raises(FormatError):
        format_device(
            dev,
            root_disk_path="/dev/nvme0n1",
            unmount_fn=failing_unmount,
            helper_invoke=fake_helper,
        )
    assert calls["helper"] == 0


def test_format_device_propagates_helper_failure():
    dev = make_device("/dev/sdb")

    def failing_helper(path, label, progress):
        raise FormatError("mkfs.vfat failed")

    with pytest.raises(FormatError):
        format_device(
            dev,
            root_disk_path="/dev/nvme0n1",
            unmount_fn=lambda d: None,
            helper_invoke=failing_helper,
        )


def test_format_device_reports_progress_messages():
    dev = make_device("/dev/sdb", mountpoints=["/media/user/SDCARD"])
    messages = []

    format_device(
        dev,
        root_disk_path="/dev/nvme0n1",
        unmount_fn=lambda d: None,
        helper_invoke=lambda *a: None,
        progress_cb=messages.append,
    )
    assert any("Unmounting" in m for m in messages)
    assert any("FAT32" in m for m in messages)
    assert any("complete" in m.lower() for m in messages)


def test_format_device_non_removable_requires_override():
    dev = make_device("/dev/sdz", removable=False)
    with pytest.raises(SafetyError):
        format_device(
            dev,
            root_disk_path="/dev/nvme0n1",
            unmount_fn=lambda d: None,
            helper_invoke=lambda *a: None,
        )
    # succeeds with explicit override
    result = format_device(
        dev,
        root_disk_path="/dev/nvme0n1",
        unmount_fn=lambda d: None,
        helper_invoke=lambda *a: None,
        allow_non_removable_override=True,
    )
    assert result.device_path == "/dev/sdz"


# --- default_helper_invoke validation (no real pkexec call is made/attempted) --------


def test_default_helper_invoke_rejects_non_device_path():
    with pytest.raises(FormatError):
        default_helper_invoke("/dev/sdb1", "STAMP")  # partition, not whole disk


def test_default_helper_invoke_rejects_path_traversal_style_input():
    with pytest.raises(FormatError):
        default_helper_invoke("/dev/../etc/passwd", "STAMP")


def test_default_helper_invoke_accepts_valid_shapes_for_validation_only(monkeypatch):
    # We don't want to actually invoke pkexec/the helper in tests; just confirm
    # a well-formed path clears the regex guard and reaches subprocess invocation,
    # which we intercept and make fail fast in a controlled way.
    import core.formatter as formatter_mod

    def fake_resolve():
        raise FormatError("stop-before-subprocess")

    monkeypatch.setattr(formatter_mod, "_resolve_helper_path", fake_resolve)
    with pytest.raises(FormatError, match="stop-before-subprocess"):
        default_helper_invoke("/dev/sdb", "STAMP")
    with pytest.raises(FormatError, match="stop-before-subprocess"):
        default_helper_invoke("/dev/mmcblk0", "STAMP")
    with pytest.raises(FormatError, match="stop-before-subprocess"):
        default_helper_invoke("/dev/nvme0n1", "STAMP")


# --- wait_for_device_node -------------------------------------------------------------


def test_wait_for_device_node_returns_immediately_when_present():
    wait_for_device_node("/dev/sdb1", attempts=1, delay_seconds=0, exists_fn=lambda p: True)


def test_wait_for_device_node_raises_after_exhausting_attempts():
    with pytest.raises(FormatError):
        wait_for_device_node("/dev/sdb1", attempts=2, delay_seconds=0, exists_fn=lambda p: False)


def test_wait_for_device_node_succeeds_after_a_few_tries():
    calls = {"n": 0}

    def exists(p):
        calls["n"] += 1
        return calls["n"] >= 3

    wait_for_device_node("/dev/sdb1", attempts=5, delay_seconds=0, exists_fn=exists)
    assert calls["n"] == 3


# --- mount_partition / unmount_partition (udisksctl, mocked runner) -------------------


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_mount_partition_parses_mount_path_from_udisksctl_output():
    def fake_runner(cmd):
        assert cmd[:2] == ["udisksctl", "mount"]
        return _completed(stdout="Mounted /dev/sdb1 at /media/user/STAMP\n")

    mount_path = mount_partition("/dev/sdb1", runner=fake_runner, exists_fn=lambda p: True)
    assert mount_path == "/media/user/STAMP"


def test_mount_partition_raises_on_udisksctl_failure():
    def fake_runner(cmd):
        return _completed(returncode=1, stderr="Error mounting: already mounted")

    with pytest.raises(FormatError, match="already mounted"):
        mount_partition("/dev/sdb1", runner=fake_runner, exists_fn=lambda p: True)


def test_mount_partition_raises_when_device_node_never_appears():
    with pytest.raises(FormatError):
        mount_partition("/dev/sdb1", runner=lambda cmd: _completed(), exists_fn=lambda p: False)


def test_mount_partition_reports_progress():
    events = []
    mount_partition(
        "/dev/sdb1",
        progress=events.append,
        runner=lambda cmd: _completed(stdout="Mounted /dev/sdb1 at /media/user/STAMP\n"),
        exists_fn=lambda p: True,
    )
    assert any("Mounting" in e for e in events)


def test_unmount_partition_succeeds():
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        return _completed()

    unmount_partition("/dev/sdb1", runner=fake_runner)
    assert calls[0][:2] == ["udisksctl", "unmount"]


def test_unmount_partition_raises_on_failure():
    def fake_runner(cmd):
        return _completed(returncode=1, stderr="target is busy")

    with pytest.raises(FormatError, match="busy"):
        unmount_partition("/dev/sdb1", runner=fake_runner)
