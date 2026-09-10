"""`stamp --cli ...` — scriptable, no-GUI entry point over the same core/ module
the GTK GUI uses (§5: "reusing the same core logic, don't duplicate").
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

from core import content_writer, device_scanner, formatter, logging_setup, safety
from core.models import Device, human_size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stamp --cli",
        description="Format an SD card as FAT32 (forced, any size) and copy a zip's contents onto it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list candidate removable devices")
    p_list.add_argument(
        "--all", action="store_true",
        help="also list the root/system disk and non-removable devices (for diagnostics; still refused by 'prepare')",
    )

    p_prepare = sub.add_parser("prepare", help="format a device as FAT32 and copy a zip's contents onto it")
    p_prepare.add_argument("--device", required=True, help="target device, e.g. /dev/sdb")
    p_prepare.add_argument("--zip", required=True, dest="zip_path", help="zip file to copy onto the card")
    p_prepare.add_argument("--label", default=formatter.DEFAULT_LABEL, help="FAT32 volume label (default: STAMP)")
    p_prepare.add_argument(
        "--yes", action="store_true",
        help="skip the interactive 'type the device path to confirm' prompt (for scripting)",
    )
    p_prepare.add_argument(
        "--allow-non-removable", action="store_true",
        help="DANGEROUS: allow operating on a device not flagged removable",
    )
    return parser


def _find_device(devices: list[Device], path: str) -> Device | None:
    return next((d for d in devices if d.path == path), None)


def cmd_list(args: argparse.Namespace) -> int:
    try:
        devices = device_scanner.scan_devices()
        root_disk = safety.get_root_disk_path()
    except (device_scanner.DeviceScanError, safety.SafetyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    candidates = devices if args.all else safety.filter_candidate_devices(devices, root_disk)

    if not candidates:
        print("No candidate devices found. Use --all to see every block device (for diagnostics).")
        return 0

    for d in candidates:
        tags = []
        if not d.removable:
            tags.append("NOT REMOVABLE")
        if safety.is_root_disk(d, root_disk):
            tags.append("ROOT DISK")
        if safety.has_protected_mountpoint(d):
            tags.append("SYSTEM MOUNT")
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        mounted = ", ".join(d.all_mountpoints) or "none"
        print(f"{d.path}\t{d.size_human}\t{d.display_model}{tag_str}\tmounted: {mounted}")
    return 0


def _print_progress(phase: str, fraction: float) -> None:
    percent = int(fraction * 100)
    sys.stdout.write(f"\r[{phase}] {percent}%  ")
    sys.stdout.flush()
    if fraction >= 1.0:
        sys.stdout.write("\n")


def cmd_prepare(args: argparse.Namespace) -> int:
    logging_setup.configure_logging()

    try:
        content_writer.validate_zip(args.zip_path)
    except content_writer.ContentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        devices = device_scanner.scan_devices()
        root_disk = safety.get_root_disk_path()
    except (device_scanner.DeviceScanError, safety.SafetyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    device = _find_device(devices, args.device)
    if device is None:
        print(f"error: {args.device} was not found among block devices (see `stamp --cli list`)", file=sys.stderr)
        return 1

    check = safety.evaluate_device_safety(device, root_disk, allow_non_removable_override=args.allow_non_removable)
    if not check.ok:
        print("error: refusing to operate on this device:", file=sys.stderr)
        for reason in check.blocking_reasons:
            print(f"  - {reason}", file=sys.stderr)
        if check.requires_override:
            print("  Re-run with --allow-non-removable to override, at your own risk.", file=sys.stderr)
        return 1
    for w in check.warnings:
        print(f"warning: {w}")

    preview = content_writer.preview_zip(args.zip_path, target_free_bytes=device.size_bytes)

    print("=== Summary ===")
    print(f"Device:  {device.path}  ({device.size_human}, {device.display_model})")
    print(f"Zip:     {args.zip_path}  ({preview.file_count} files, {human_size(preview.uncompressed_size_bytes)})")
    print(f"Label:   {formatter.sanitize_label(args.label)}")
    for w in preview.warnings:
        print(f"warning: {w}")
    print()
    print(f"THIS WILL ERASE EVERYTHING ON {device.path}.")

    if not args.yes:
        typed = input(f"Type the device path ({device.path}) to confirm, or press Enter to cancel: ")
        if not safety.confirmation_text_matches(device.path, typed):
            print("Confirmation did not match. Aborted; nothing was touched.")
            return 1

    try:
        result = formatter.format_device(
            device,
            root_disk,
            label=args.label,
            allow_non_removable_override=args.allow_non_removable,
            progress_cb=lambda msg: print(f"[format] {msg}"),
        )
    except (formatter.FormatError, safety.SafetyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    tmp_extract = tempfile.mkdtemp(prefix="stamp-extract-")
    mount_path: str | None = None
    try:
        content_writer.extract_zip(args.zip_path, tmp_extract, progress_cb=_print_progress)

        mount_path = formatter.mount_partition(result.partition_path, progress=lambda m: print(f"[mount] {m}"))

        content_writer.copy_tree_rsync(tmp_extract, mount_path, progress_cb=_print_progress)
        content_writer.sync_and_flush()

        top_level = os.listdir(tmp_extract)
        missing = content_writer.verify_top_level_entries(mount_path, top_level)
        if missing:
            print(f"error: verification failed — missing after copy: {missing}", file=sys.stderr)
            return 1
    except (content_writer.ContentError, formatter.FormatError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        content_writer.cleanup_temp_dir(tmp_extract)
        if mount_path:
            try:
                formatter.unmount_partition(result.partition_path)
            except formatter.FormatError as exc:
                print(f"warning: failed to cleanly unmount {result.partition_path}: {exc}", file=sys.stderr)

    print()
    print(f"Done. {device.path} is formatted FAT32 (label {result.label}) and loaded.")
    print("It is now safe to physically remove the card.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        return cmd_list(args)
    if args.command == "prepare":
        try:
            return cmd_prepare(args)
        except KeyboardInterrupt:
            print("\ncancelled.", file=sys.stderr)
            return 130
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
