import os
import zipfile

import pytest

from core.content_writer import (
    ContentError,
    copy_tree_rsync,
    extract_zip,
    preview_zip,
    validate_zip,
    verify_top_level_entries,
)


def make_zip(path, files: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return str(path)


# --- validate_zip -------------------------------------------------------------


def test_validate_zip_accepts_good_zip(tmp_path):
    zpath = make_zip(tmp_path / "good.zip", {"a.txt": b"hello", "dir/b.txt": b"world"})
    validate_zip(zpath)  # should not raise


def test_validate_zip_rejects_missing_file(tmp_path):
    with pytest.raises(ContentError):
        validate_zip(str(tmp_path / "nope.zip"))


def test_validate_zip_rejects_non_zip(tmp_path):
    bogus = tmp_path / "bogus.zip"
    bogus.write_text("not a zip")
    with pytest.raises(ContentError):
        validate_zip(str(bogus))


def test_validate_zip_rejects_empty_zip(tmp_path):
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(ContentError):
        validate_zip(str(empty))


# --- preview_zip ----------------------------------------------------------------


def test_preview_zip_counts_files_and_size(tmp_path):
    zpath = make_zip(
        tmp_path / "content.zip",
        {"a.txt": b"1234567890", "sub/b.txt": b"12345", "sub/nested/c.txt": b"1"},
    )
    preview = preview_zip(zpath)
    assert preview.file_count == 3
    assert preview.uncompressed_size_bytes == 16
    assert preview.top_level_names == ["a.txt", "sub"]
    assert preview.warnings == []


def test_preview_zip_warns_when_exceeds_free_space(tmp_path):
    zpath = make_zip(tmp_path / "big.zip", {"a.txt": b"x" * 1000})
    preview = preview_zip(zpath, target_free_bytes=10)
    assert preview.warnings
    assert "exceed" in preview.warnings[0]


def test_preview_zip_no_warning_when_fits(tmp_path):
    zpath = make_zip(tmp_path / "small.zip", {"a.txt": b"x" * 10})
    preview = preview_zip(zpath, target_free_bytes=10_000)
    assert preview.warnings == []


def test_preview_zip_ignores_directory_entries(tmp_path):
    zpath = tmp_path / "withdirs.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("sub/", "")  # explicit directory entry
        zf.writestr("sub/file.txt", "hello")
    preview = preview_zip(str(zpath))
    assert preview.file_count == 1


# --- extract_zip ------------------------------------------------------------------


def test_extract_zip_preserves_structure(tmp_path):
    zpath = make_zip(
        tmp_path / "content.zip",
        {"a.txt": b"hello", "sub/b.txt": b"world"},
    )
    dest = tmp_path / "out"
    progress_events = []
    extract_zip(zpath, str(dest), progress_cb=lambda phase, frac: progress_events.append((phase, frac)))

    assert (dest / "a.txt").read_bytes() == b"hello"
    assert (dest / "sub" / "b.txt").read_bytes() == b"world"
    assert progress_events[0] == ("extract", 0.0)
    assert progress_events[-1] == ("extract", 1.0)
    assert all(phase == "extract" for phase, _ in progress_events)


def test_extract_zip_rejects_corrupt_zip(tmp_path):
    bogus = tmp_path / "bad.zip"
    bogus.write_bytes(b"PK\x03\x04not really a zip")
    with pytest.raises(ContentError):
        extract_zip(str(bogus), str(tmp_path / "out"))


# --- copy_tree_rsync ---------------------------------------------------------------


def test_copy_tree_rsync_copies_files_and_reports_completion(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("hello")
    (src / "sub" / "b.txt").write_text("world")
    dest = tmp_path / "dest"
    dest.mkdir()

    events = []
    copy_tree_rsync(str(src), str(dest), progress_cb=lambda phase, frac: events.append((phase, frac)))

    assert (dest / "a.txt").read_text() == "hello"
    assert (dest / "sub" / "b.txt").read_text() == "world"
    assert events[0] == ("copy", 0.0)
    assert events[-1] == ("copy", 1.0)


def test_copy_tree_rsync_missing_source_raises(tmp_path):
    with pytest.raises(ContentError):
        copy_tree_rsync(str(tmp_path / "nope"), str(tmp_path), progress_cb=None)


def test_copy_tree_rsync_missing_dest_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(ContentError):
        copy_tree_rsync(str(src), str(tmp_path / "nope_dest"), progress_cb=None)


# --- verify_top_level_entries --------------------------------------------------------


def test_verify_top_level_entries_all_present(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    missing = verify_top_level_entries(str(tmp_path), ["a.txt", "sub"])
    assert missing == []


def test_verify_top_level_entries_reports_missing(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    missing = verify_top_level_entries(str(tmp_path), ["a.txt", "sub"])
    assert missing == ["sub"]


def test_verify_top_level_entries_bad_path_raises(tmp_path):
    with pytest.raises(ContentError):
        verify_top_level_entries(str(tmp_path / "doesnotexist"), ["a.txt"])
