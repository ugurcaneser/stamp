import json

from core.device_scanner import DeviceScanError, parse_lsblk_json, scan_devices

# A realistic lsblk -J -b -o NAME,PATH,SIZE,MODEL,VENDOR,RM,RO,TYPE,MOUNTPOINTS,FSTYPE,LABEL,TRAN
# capture: an NVMe boot disk (with root + EFI partitions) and a 64GB USB SD reader,
# plus a second, unmounted USB stick.
SAMPLE_LSBLK = {
    "blockdevices": [
        {
            "name": "nvme0n1",
            "path": "/dev/nvme0n1",
            "size": "512110190592",
            "model": "SAMSUNG MZVL2512",
            "vendor": None,
            "rm": "0",
            "ro": "0",
            "type": "disk",
            "mountpoints": [None],
            "fstype": None,
            "label": None,
            "tran": "nvme",
            "children": [
                {
                    "name": "nvme0n1p1",
                    "path": "/dev/nvme0n1p1",
                    "size": "1073741824",
                    "fstype": "vfat",
                    "mountpoints": ["/boot/efi"],
                    "label": "EFI",
                    "type": "part",
                },
                {
                    "name": "nvme0n1p2",
                    "path": "/dev/nvme0n1p2",
                    "size": "511035won",
                    "fstype": "ext4",
                    "mountpoints": ["/"],
                    "label": None,
                    "type": "part",
                },
            ],
        },
        {
            "name": "sdb",
            "path": "/dev/sdb",
            "size": "63864029184",
            "model": "SD Card Reader",
            "vendor": "Generic",
            "rm": "1",
            "ro": "0",
            "type": "disk",
            "mountpoints": [None],
            "fstype": None,
            "label": None,
            "tran": "usb",
            "children": [
                {
                    "name": "sdb1",
                    "path": "/dev/sdb1",
                    "size": "63864000000",
                    "fstype": "exfat",
                    "mountpoints": ["/media/user/SDCARD"],
                    "label": "SDCARD",
                    "type": "part",
                },
            ],
        },
        {
            "name": "sdc",
            "path": "/dev/sdc",
            "size": "16000000000",
            "model": "Cruzer",
            "vendor": "SanDisk",
            "rm": "1",
            "ro": "0",
            "type": "disk",
            "mountpoints": [None],
            "fstype": None,
            "label": None,
            "tran": "usb",
            "children": [],
        },
    ]
}


def _fix_sample():
    # correct the deliberately-broken size above (kept broken to prove the
    # parser doesn't blow up on a malformed numeric field, then fixed here
    # for the tests that need real sizes)
    data = json.loads(json.dumps(SAMPLE_LSBLK))
    data["blockdevices"][0]["children"][1]["size"] = "511035801600"
    return data


def test_parse_lsblk_json_returns_only_whole_disks():
    devices = parse_lsblk_json(json.dumps(_fix_sample()))
    assert [d.path for d in devices] == ["/dev/nvme0n1", "/dev/sdb", "/dev/sdc"]


def test_parse_lsblk_json_captures_partitions_and_mountpoints():
    devices = parse_lsblk_json(json.dumps(_fix_sample()))
    nvme = next(d for d in devices if d.path == "/dev/nvme0n1")
    assert [p.path for p in nvme.partitions] == ["/dev/nvme0n1p1", "/dev/nvme0n1p2"]
    root_part = next(p for p in nvme.partitions if p.path == "/dev/nvme0n1p2")
    assert root_part.mountpoints == ["/"]
    assert root_part.fstype == "ext4"


def test_parse_lsblk_json_removable_flag():
    devices = parse_lsblk_json(json.dumps(_fix_sample()))
    nvme = next(d for d in devices if d.path == "/dev/nvme0n1")
    sdb = next(d for d in devices if d.path == "/dev/sdb")
    assert nvme.removable is False
    assert sdb.removable is True


def test_parse_lsblk_json_device_with_no_partitions():
    devices = parse_lsblk_json(json.dumps(_fix_sample()))
    sdc = next(d for d in devices if d.path == "/dev/sdc")
    assert sdc.partitions == []
    assert sdc.has_mounted_partitions is False


def test_parse_lsblk_json_malformed_size_does_not_raise():
    # nvme0n1p1's size is a valid int in the fixed sample; use the raw (broken)
    # sample to prove a garbage numeric field degrades to 0 instead of crashing.
    devices = parse_lsblk_json(json.dumps(SAMPLE_LSBLK))
    nvme = next(d for d in devices if d.path == "/dev/nvme0n1")
    root_part = next(p for p in nvme.partitions if p.path == "/dev/nvme0n1p2")
    assert root_part.size_bytes == 0  # "511035won" isn't a valid int


def test_parse_lsblk_json_invalid_json_raises_device_scan_error():
    try:
        parse_lsblk_json("not json")
        assert False, "expected DeviceScanError"
    except DeviceScanError:
        pass


def test_scan_devices_uses_injected_runner():
    calls = []

    def fake_runner():
        calls.append(1)
        return json.dumps(_fix_sample())

    devices = scan_devices(lsblk_runner=fake_runner)
    assert len(devices) == 3
    assert len(calls) == 1


def test_human_size_formatting():
    from core.models import human_size

    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(63864029184) == "59.5 GB"
