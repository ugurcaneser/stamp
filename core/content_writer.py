"""Zip validation/preview, extraction, and copy-to-card logic.

Two visible phases per §4.4: extraction (zip -> temp dir) and copy
(temp dir -> mounted card, via rsync). Both report progress through a
callback instead of blocking silently.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("stamp")

ProgressCallback = Callable[[str, float], None]  # (phase, fraction 0.0-1.0)


class ContentError(RuntimeError):
    """Raised for a bad zip, extraction failure, or copy failure."""


@dataclass
class ZipPreview:
    file_count: int
    uncompressed_size_bytes: int
    top_level_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_zip(zip_path: str) -> None:
    """Fail fast (§4.2): confirm the zip is readable and non-empty before anything else."""
    if not os.path.isfile(zip_path):
        raise ContentError(f"{zip_path} does not exist or is not a file")
    if not zipfile.is_zipfile(zip_path):
        raise ContentError(f"{zip_path} is not a valid zip file")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad_entry = zf.testzip()
            if bad_entry is not None:
                raise ContentError(f"{zip_path} is corrupt (bad entry: {bad_entry})")
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise ContentError(f"{zip_path} is corrupt: {exc}") from exc
    if not names:
        raise ContentError(f"{zip_path} is empty")


def preview_zip(zip_path: str, target_free_bytes: int | None = None) -> ZipPreview:
    """Summarize a zip's contents: file count, uncompressed size, top-level entries.

    Warns (but does not raise) if the uncompressed size exceeds target_free_bytes,
    per §4.2 — the caller decides whether that warning should block proceeding.
    """
    validate_zip(zip_path)
    warnings: list[str] = []
    top_level: set[str] = set()
    total_size = 0
    file_count = 0

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            file_count += 1
            total_size += info.file_size
            top = info.filename.split("/", 1)[0]
            if top:
                top_level.add(top)

    if target_free_bytes is not None and total_size > target_free_bytes:
        warnings.append(
            f"Uncompressed contents ({_human(total_size)}) exceed the target's free space "
            f"({_human(target_free_bytes)})."
        )

    return ZipPreview(
        file_count=file_count,
        uncompressed_size_bytes=total_size,
        top_level_names=sorted(top_level),
        warnings=warnings,
    )


def _human(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def extract_zip(zip_path: str, dest_dir: str, progress_cb: ProgressCallback | None = None) -> str:
    """Extract zip_path into dest_dir, reporting fractional progress by bytes extracted.

    Returns dest_dir. Raises ContentError on failure (corrupt entry, disk full, etc).
    """
    validate_zip(zip_path)
    os.makedirs(dest_dir, exist_ok=True)

    def report(fraction: float) -> None:
        if progress_cb:
            progress_cb("extract", fraction)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos) or 1
            done = 0
            report(0.0)
            for info in infos:
                zf.extract(info, dest_dir)
                done += info.file_size
                report(min(done / total, 1.0))
            report(1.0)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ContentError(f"failed to extract {zip_path}: {exc}") from exc

    return dest_dir


_RSYNC_PERCENT_RE = re.compile(r"(\d+)%")


def copy_tree_rsync(
    src_dir: str,
    dest_dir: str,
    progress_cb: ProgressCallback | None = None,
) -> None:
    """Copy src_dir's contents into dest_dir with rsync, preserving structure (§4.4).

    Parses rsync's --info=progress2 output for the overall percentage so the
    GUI can show real copy progress instead of a spinner.
    """
    if not os.path.isdir(src_dir):
        raise ContentError(f"{src_dir} does not exist")
    if not os.path.isdir(dest_dir):
        raise ContentError(f"copy destination {dest_dir} does not exist (is the card mounted?)")

    src = src_dir.rstrip("/") + "/"
    log.info("copying %s -> %s via rsync", src, dest_dir)

    def report(fraction: float) -> None:
        if progress_cb:
            progress_cb("copy", fraction)

    report(0.0)

    def run(args: list[str]) -> subprocess.Popen:
        try:
            return subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
        except FileNotFoundError as exc:
            raise ContentError("rsync is not installed") from exc

    # --info=progress2 needs rsync >= 3.1 (Ubuntu 22.04+ ships 3.2+). Older/BSD
    # rsync rejects the flag outright; fall back to a plain copy with no
    # incremental percentage rather than failing the whole operation.
    proc = run(["rsync", "-a", "--info=progress2", src, dest_dir])
    output_lines: list[str] = []
    saw_percent = False
    assert proc.stdout is not None
    for line in proc.stdout:
        output_lines.append(line)
        match = _RSYNC_PERCENT_RE.search(line)
        if match:
            saw_percent = True
            report(min(int(match.group(1)) / 100.0, 1.0))
    returncode = proc.wait()

    if returncode != 0 and not saw_percent:
        combined = "".join(output_lines)
        if "unknown option" in combined or "invalid option" in combined or "--info" in combined:
            proc = run(["rsync", "-a", src, dest_dir])
            output_lines = list(proc.stdout) if proc.stdout else []
            returncode = proc.wait()

    if returncode != 0:
        tail = "".join(output_lines[-20:])
        log.error("rsync failed (exit %s): %s", returncode, tail.strip())
        raise ContentError(f"rsync failed (exit {returncode}):\n{tail}")
    log.info("copy complete: %s -> %s", src, dest_dir)
    report(1.0)


def sync_and_flush(target_path: str | None = None) -> None:
    """Force pending writes to physical media (§4.4: `sync` before telling the user it's safe)."""
    try:
        subprocess.run(["sync"], check=True, timeout=120)
    except FileNotFoundError as exc:
        raise ContentError("sync is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise ContentError(f"sync failed: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ContentError("sync timed out") from exc


def verify_top_level_entries(mount_path: str, expected_top_level: list[str]) -> list[str]:
    """Spot-check (§4.6) that the expected top-level entries exist post-copy.

    Returns the list of any expected entries that are missing (empty list = all present).
    Caller should treat a non-empty result as a verification failure.
    """
    try:
        actual = set(os.listdir(mount_path))
    except OSError as exc:
        raise ContentError(f"could not read back {mount_path} to verify the write: {exc}") from exc
    return [name for name in expected_top_level if name not in actual]


def cleanup_temp_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
