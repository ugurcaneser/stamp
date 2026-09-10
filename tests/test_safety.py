from core.models import Device, Partition
from core.safety import (
    checkbox_confirms,
    confirmation_text_matches,
    evaluate_device_safety,
    filter_candidate_devices,
    get_root_disk_path,
    has_protected_mountpoint,
    is_root_disk,
)


def make_device(path, removable=True, read_only=False, mountpoints=None, name=None):
    parts = []
    if mountpoints:
        parts.append(
            Partition(
                path=f"{path}1",
                name=(name or path.split("/")[-1]) + "1",
                size_bytes=1000,
                fstype="ext4",
                mountpoints=mountpoints,
            )
        )
    return Device(
        path=path,
        name=name or path.split("/")[-1],
        size_bytes=64_000_000_000,
        model="Test Device",
        vendor="Test Vendor",
        removable=removable,
        read_only=read_only,
        transport="usb",
        partitions=parts,
    )


# --- get_root_disk_path -----------------------------------------------------


def test_get_root_disk_path_resolves_partition_to_parent_disk():
    root = get_root_disk_path(
        findmnt_runner=lambda: "/dev/nvme0n1p2",
        pkname_runner=lambda part: "nvme0n1",
    )
    assert root == "/dev/nvme0n1"


def test_get_root_disk_path_root_directly_on_disk():
    root = get_root_disk_path(
        findmnt_runner=lambda: "/dev/sda",
        pkname_runner=lambda part: "",
    )
    assert root == "/dev/sda"


def test_get_root_disk_path_non_dev_source_passthrough():
    # e.g. overlayfs in a container: nothing to protect against
    root = get_root_disk_path(
        findmnt_runner=lambda: "overlay",
        pkname_runner=lambda part: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    assert root == "overlay"


# --- is_root_disk / has_protected_mountpoint --------------------------------


def test_is_root_disk_true_for_exact_match():
    dev = make_device("/dev/nvme0n1")
    assert is_root_disk(dev, "/dev/nvme0n1") is True


def test_is_root_disk_false_for_other_device():
    dev = make_device("/dev/sdb")
    assert is_root_disk(dev, "/dev/nvme0n1") is False


def test_has_protected_mountpoint_root():
    dev = make_device("/dev/nvme0n1", mountpoints=["/"])
    assert has_protected_mountpoint(dev) is True


def test_has_protected_mountpoint_boot_efi():
    dev = make_device("/dev/nvme0n1", mountpoints=["/boot/efi"])
    assert has_protected_mountpoint(dev) is True


def test_has_protected_mountpoint_home():
    dev = make_device("/dev/sdd", mountpoints=["/home"])
    assert has_protected_mountpoint(dev) is True


def test_has_protected_mountpoint_false_for_plain_removable():
    dev = make_device("/dev/sdb", mountpoints=["/media/user/SDCARD"])
    assert has_protected_mountpoint(dev) is False


def test_has_protected_mountpoint_false_for_unmounted():
    dev = make_device("/dev/sdc")
    assert has_protected_mountpoint(dev) is False


def test_has_protected_mountpoint_does_not_false_positive_on_similar_prefix():
    # /homework is not /home
    dev = make_device("/dev/sde", mountpoints=["/homework"])
    assert has_protected_mountpoint(dev) is False


# --- evaluate_device_safety --------------------------------------------------


def test_evaluate_device_safety_blocks_root_disk():
    dev = make_device("/dev/nvme0n1", mountpoints=["/"])
    result = evaluate_device_safety(dev, root_disk_path="/dev/nvme0n1")
    assert result.ok is False
    assert any("running operating system" in r for r in result.blocking_reasons)


def test_evaluate_device_safety_blocks_protected_mountpoint_even_if_not_root_path():
    # separate /home disk, distinct from the root disk
    dev = make_device("/dev/sdd", mountpoints=["/home"])
    result = evaluate_device_safety(dev, root_disk_path="/dev/nvme0n1")
    assert result.ok is False


def test_evaluate_device_safety_allows_clean_removable_device():
    dev = make_device("/dev/sdb", removable=True, mountpoints=["/media/user/SDCARD"])
    result = evaluate_device_safety(dev, root_disk_path="/dev/nvme0n1")
    assert result.ok is True
    assert result.blocking_reasons == []


def test_evaluate_device_safety_blocks_non_removable_without_override():
    dev = make_device("/dev/sdz", removable=False)
    result = evaluate_device_safety(dev, root_disk_path="/dev/nvme0n1")
    assert result.ok is False
    assert result.requires_override is True


def test_evaluate_device_safety_allows_non_removable_with_override():
    dev = make_device("/dev/sdz", removable=False)
    result = evaluate_device_safety(dev, root_disk_path="/dev/nvme0n1", allow_non_removable_override=True)
    assert result.ok is True
    assert result.warnings  # user should still see a warning was acknowledged


def test_evaluate_device_safety_override_cannot_rescue_root_disk():
    dev = make_device("/dev/nvme0n1", removable=False, mountpoints=["/"])
    result = evaluate_device_safety(dev, root_disk_path="/dev/nvme0n1", allow_non_removable_override=True)
    assert result.ok is False


def test_evaluate_device_safety_blocks_read_only():
    dev = make_device("/dev/sdb", read_only=True)
    result = evaluate_device_safety(dev, root_disk_path="/dev/nvme0n1")
    assert result.ok is False


# --- filter_candidate_devices -------------------------------------------------


def test_filter_candidate_devices_excludes_root_and_protected():
    root_disk = make_device("/dev/nvme0n1", removable=False, mountpoints=["/"])
    home_disk = make_device("/dev/sdd", removable=False, mountpoints=["/home"])
    sd_card = make_device("/dev/sdb", removable=True, mountpoints=["/media/user/SDCARD"])

    result = filter_candidate_devices([root_disk, home_disk, sd_card], root_disk_path="/dev/nvme0n1")
    assert [d.path for d in result] == ["/dev/sdb"]


# --- confirmation helpers ------------------------------------------------------


def test_confirmation_text_matches_exact():
    assert confirmation_text_matches("/dev/sdb", "/dev/sdb") is True


def test_confirmation_text_matches_trims_whitespace():
    assert confirmation_text_matches("/dev/sdb", "  /dev/sdb  ") is True


def test_confirmation_text_matches_rejects_mismatch():
    assert confirmation_text_matches("/dev/sdb", "/dev/sdc") is False


def test_confirmation_text_matches_rejects_empty():
    assert confirmation_text_matches("/dev/sdb", "") is False


def test_checkbox_confirms():
    assert checkbox_confirms(True) is True
    assert checkbox_confirms(False) is False
