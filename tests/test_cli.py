import pytest

import cli.main as cli
from core.models import Device


def make_device(path, removable=True, mountpoints=None, name=None):
    from core.models import Partition

    parts = []
    if mountpoints:
        parts.append(Partition(path=f"{path}1", name=(name or path) + "1", size_bytes=1000, fstype="exfat", mountpoints=mountpoints))
    return Device(
        path=path, name=name or path.split("/")[-1], size_bytes=64_000_000_000,
        model="Card Reader", vendor="Generic", removable=removable, read_only=False,
        transport="usb", partitions=parts,
    )


def test_build_parser_requires_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_list():
    parser = cli.build_parser()
    args = parser.parse_args(["list"])
    assert args.command == "list"
    assert args.all is False


def test_build_parser_prepare_requires_device_and_zip():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["prepare"])
    args = parser.parse_args(["prepare", "--device", "/dev/sdb", "--zip", "x.zip"])
    assert args.device == "/dev/sdb"
    assert args.zip_path == "x.zip"
    assert args.label == "STAMP"
    assert args.yes is False
    assert args.allow_non_removable is False


def test_cmd_list_filters_root_and_protected(monkeypatch, capsys):
    root = make_device("/dev/nvme0n1", removable=False, mountpoints=["/"])
    sdcard = make_device("/dev/sdb", mountpoints=["/media/user/SDCARD"])

    monkeypatch.setattr(cli.device_scanner, "scan_devices", lambda: [root, sdcard])
    monkeypatch.setattr(cli.safety, "get_root_disk_path", lambda: "/dev/nvme0n1")

    rc = cli.cmd_list(argparse_ns(all=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "/dev/sdb" in out
    assert "/dev/nvme0n1" not in out


def test_cmd_list_all_shows_tags(monkeypatch, capsys):
    root = make_device("/dev/nvme0n1", removable=False, mountpoints=["/"])
    monkeypatch.setattr(cli.device_scanner, "scan_devices", lambda: [root])
    monkeypatch.setattr(cli.safety, "get_root_disk_path", lambda: "/dev/nvme0n1")

    rc = cli.cmd_list(argparse_ns(all=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ROOT DISK" in out


def test_cmd_prepare_rejects_unknown_device(monkeypatch, tmp_path, capsys):
    zpath = tmp_path / "x.zip"
    import zipfile
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("a.txt", "hi")

    monkeypatch.setattr(cli.device_scanner, "scan_devices", lambda: [])
    monkeypatch.setattr(cli.safety, "get_root_disk_path", lambda: "/dev/nvme0n1")
    monkeypatch.setattr(cli.logging_setup, "configure_logging", lambda: None)

    args = argparse_ns(device="/dev/sdb", zip_path=str(zpath), label="STAMP", yes=True, allow_non_removable=False)
    rc = cli.cmd_prepare(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err


def test_cmd_prepare_rejects_root_disk(monkeypatch, tmp_path, capsys):
    import zipfile
    zpath = tmp_path / "x.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("a.txt", "hi")

    root = make_device("/dev/nvme0n1", removable=False, mountpoints=["/"])
    monkeypatch.setattr(cli.device_scanner, "scan_devices", lambda: [root])
    monkeypatch.setattr(cli.safety, "get_root_disk_path", lambda: "/dev/nvme0n1")
    monkeypatch.setattr(cli.logging_setup, "configure_logging", lambda: None)

    args = argparse_ns(device="/dev/nvme0n1", zip_path=str(zpath), label="STAMP", yes=True, allow_non_removable=False)
    rc = cli.cmd_prepare(args)
    err = capsys.readouterr().err
    assert rc == 1
    assert "refusing" in err.lower()


def argparse_ns(**kwargs):
    import argparse
    return argparse.Namespace(**kwargs)
