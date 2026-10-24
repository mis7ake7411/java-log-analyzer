import pytest

from log_analyzer.application.source_preview import read_source_context
from log_analyzer.domain.log_types import OccurrenceCoordinate, SourceSnapshot


def _snapshot(path, *, stable=True) -> SourceSnapshot:
    stat_result = path.stat()
    return {
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "stable": stable,
    }


def _occurrence(filename, line_number=5) -> OccurrenceCoordinate:
    return {
        "filename": filename,
        "line_number": line_number,
        "timestamp": "2026-06-06 10:00:00.001",
        "thread": "main",
    }


def test_preview_reads_three_lines_each_side(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    source_file = log_dir / "app.log"
    source_file.write_text(
        "line-1\nline-2\nline-3\nline-4\nline-5\nline-6\nline-7\nline-8\nline-9\n",
        encoding="utf-8",
    )

    preview = read_source_context(
        str(log_dir),
        _occurrence("app.log"),
        _snapshot(source_file),
    )

    assert preview.lines == (
        (2, "line-2"),
        (3, "line-3"),
        (4, "line-4"),
        (5, "line-5"),
        (6, "line-6"),
        (7, "line-7"),
        (8, "line-8"),
    )
    assert preview.match_line == 5
    assert preview.path_line == f"{source_file.resolve()}:5"
    assert preview.warning is None


def test_preview_displays_replacement_and_nonfatal_encoding_notice(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    source_file = log_dir / "app.log"
    source_file.write_bytes(b"before\nmatched \x80 line\nafter\n")

    preview = read_source_context(
        str(log_dir),
        _occurrence("app.log", 2),
        _snapshot(source_file),
        radius=1,
    )

    assert preview.lines == (
        (1, "before"),
        (2, "matched \ufffd line"),
        (3, "after"),
    )
    assert preview.warning is None
    assert preview.encoding_notice is not None


def test_warning_preview_has_no_copyable_context(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    source_file = log_dir / "app.log"
    source_file.write_text("line-1\nline-2\nline-3\nline-4\nline-5\n", encoding="utf-8")

    preview = read_source_context(
        str(log_dir),
        _occurrence("app.log"),
        _snapshot(source_file, stable=False),
    )

    assert preview.warning is not None
    assert preview.lines == ()


@pytest.mark.parametrize("failure", ["missing", "changed", "unstable", "missing_snapshot"])
def test_preview_rejects_missing_changed_or_unstable_file(tmp_path, failure):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    source_file = log_dir / "app.log"
    source_file.write_text("line-1\nline-2\nline-3\nline-4\nline-5\n", encoding="utf-8")
    snapshot = _snapshot(source_file)

    if failure == "missing":
        source_file.unlink()
    elif failure == "changed":
        source_file.write_text("changed source content\n", encoding="utf-8")
    elif failure == "unstable":
        snapshot["stable"] = False
    else:
        snapshot = None

    preview = read_source_context(str(log_dir), _occurrence("app.log"), snapshot)

    assert preview.lines == ()
    assert preview.warning is not None


@pytest.mark.parametrize("target_kind", ["traversal", "outside_symlink"])
def test_preview_rejects_path_traversal_and_outside_symlink(tmp_path, target_kind):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    outside_file = tmp_path / "outside.log"
    outside_file.write_text("outside secret\n", encoding="utf-8")
    snapshot = _snapshot(outside_file)

    if target_kind == "traversal":
        filename = "../outside.log"
    else:
        symlink = log_dir / "escape.log"
        try:
            symlink.symlink_to(outside_file)
        except (NotImplementedError, OSError):
            pytest.skip("Windows symlink support is unavailable")
        filename = "escape.log"

    preview = read_source_context(str(log_dir), _occurrence(filename), snapshot)

    assert preview.lines == ()
    assert preview.warning is not None
