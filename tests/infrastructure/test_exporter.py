from pathlib import Path
import csv
import json

import pytest

from log_analyzer.infrastructure.exporting import (
    ExportConflictError,
    ExportOptions,
    export_results,
    export_with_options,
)
from log_analyzer.infrastructure.exporting import exporter


def _build_log(index: int, payload: str) -> dict:
    return {
        "count": 1,
        "line_numbers": [index],
        "timestamp": f"2026-06-07 12:00:{index:02d}.000",
        "last_timestamp": f"2026-06-07 12:00:{index:02d}.000",
        "level": "INFO",
        "thread": "main",
        "logger": "com.test.App",
        "filename": "app.log",
        "message": f"Message {index} {payload}",
        "message_body": f"Body {index} {payload}",
        "stacktrace": "",
    }


def _build_level_log(index: int, level: str, payload: str = "short") -> dict:
    log = _build_log(index, payload)
    log["level"] = level
    log["message"] = f"{level} message {index} {payload}"
    log["message_body"] = f"{level} body {index} {payload}"
    return log


def _build_occurrence_log(index: int, level: str = "INFO", payload: str = "short") -> dict:
    log = _build_level_log(index, level, payload)
    log["count"] = 2
    log["line_numbers"] = [index * 10 + 1, index * 10 + 2]
    log["message"] = f"group {index}"
    log["message_body"] = payload
    log["occurrences"] = [
        {
            "filename": f"source {index}-一.log",
            "line_number": index * 10 + 1,
            "timestamp": f"2026-06-07 12:00:{index:02d}.000",
            "thread": f"worker-{index}-a",
        },
        {
            "filename": f'archive/{index}-"quoted, source".log',
            "line_number": index * 10 + 2,
            "timestamp": f"2026-06-07 12:01:{index:02d}.000",
            "thread": f"worker-{index}-b",
        },
    ]
    return log


def _read_exported_coordinates(file_path: Path, fmt: str) -> list[str]:
    if fmt == "json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return [
            f"{occurrence['filename']}:{occurrence['line_number']} | "
            f"{occurrence['timestamp']} | {occurrence['thread']}"
            for log in data["results"]
            for occurrence in log.get("occurrences", [])
        ]

    if fmt == "csv":
        with file_path.open(encoding="utf-8-sig", newline="") as report:
            rows = list(csv.reader(report))
        return [
            coordinate
            for row in rows
            if len(row) >= 12 and row[8].startswith("group ")
            for coordinate in row[-1].splitlines()
        ]

    return [
        line[4:]
        for line in file_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("  - ") and " | " in line
    ]


class _FailingHandle:
    def __init__(self) -> None:
        self.closed = False

    def write(self, _text: str) -> None:
        raise OSError("disk full")

    def close(self) -> None:
        self.closed = True


def test_open_part_file_closes_handle_when_prefix_write_fails(monkeypatch, tmp_path):
    handle = _FailingHandle()
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: handle)

    with pytest.raises(OSError, match="disk full"):
        exporter._open_part_file(tmp_path / "report.csv", {"INFO": 1}, "csv", "utf-8-sig")

    assert handle.closed is True


def test_close_part_file_closes_handle_when_suffix_write_fails():
    handle = _FailingHandle()

    with pytest.raises(OSError, match="disk full"):
        exporter._close_part_file(handle, "json")

    assert handle.closed is True


def test_export_with_options_matches_legacy_wrapper(tmp_path):
    counts = {"ERROR": 2, "WARN": 1}
    logs = [
        _build_level_log(1, "ERROR", "X" * 900),
        _build_level_log(2, "ERROR", "X" * 900),
        _build_level_log(3, "WARN"),
    ]
    legacy_output = tmp_path / "legacy" / "report.csv"
    options_output = tmp_path / "options" / "report.csv"
    legacy_output.parent.mkdir()
    options_output.parent.mkdir()

    legacy_files = export_results(
        counts,
        logs,
        str(legacy_output),
        "csv",
        max_export_bytes=5_000,
        split_by_level=True,
    )
    options_files = export_with_options(
        ExportOptions(
            counts=counts,
            matched_logs=logs,
            output_path=str(options_output),
            format="csv",
            max_export_bytes=5_000,
            split_by_level=True,
        )
    )

    assert [Path(path).name for path in options_files] == [Path(path).name for path in legacy_files]
    assert [
        Path(path).read_text(encoding="utf-8-sig").replace(str(options_output.parent), "<output>")
        for path in options_files
    ] == [
        Path(path).read_text(encoding="utf-8-sig").replace(str(legacy_output.parent), "<output>")
        for path in legacy_files
    ]


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_export_results_keeps_single_file_when_within_limit(tmp_path, monkeypatch, fmt, expected_suffix):
    monkeypatch.setattr("log_analyzer.infrastructure.exporting.exporter.MAX_EXPORT_BYTES_PER_FILE", 10_000)
    output_path = tmp_path / f"report{expected_suffix}"

    files = export_results({"INFO": 1}, [_build_log(1, "short")], str(output_path), fmt)

    assert files == [str(output_path)]
    assert output_path.exists()


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_export_results_splits_large_reports(tmp_path, monkeypatch, fmt, expected_suffix):
    monkeypatch.setattr("log_analyzer.infrastructure.exporting.exporter.MAX_EXPORT_BYTES_PER_FILE", 5_000)
    output_path = tmp_path / f"report{expected_suffix}"
    payload = "X" * 900

    files = export_results(
        {"INFO": 3},
        [_build_log(1, payload), _build_log(2, payload), _build_log(3, payload)],
        str(output_path),
        fmt,
    )

    assert files == [
        str(tmp_path / f"report_summary{expected_suffix}"),
        str(tmp_path / f"report_part001{expected_suffix}"),
        str(tmp_path / f"report_part002{expected_suffix}"),
    ]
    for file_path in files:
        assert Path(file_path).exists()

    summary_text = Path(files[0]).read_text(encoding="utf-8")
    assert "report_part001" in summary_text
    assert "report_part002" in summary_text

    if fmt == "json":
        for file_path in files:
            if file_path.endswith(".json"):
                json.loads(Path(file_path).read_text(encoding="utf-8"))

    for file_path in files[1:]:
        assert Path(file_path).stat().st_size <= 5_000


def _size_split_threshold(logs, levels, counts, fmt, split_by_level):
    prefix_counts = {levels[0]: 4} if split_by_level else counts
    prefix_size = exporter._rendered_size(exporter._report_prefix(prefix_counts, fmt), fmt)
    first_size = exporter._render_log_block(1, logs[0], fmt, False)[1]
    second_size = exporter._render_log_block(2, logs[1], fmt, True)[1]
    threshold = prefix_size + first_size + second_size

    for level in levels:
        level_counts = {level: 4} if split_by_level else counts
        level_prefix_size = exporter._rendered_size(
            exporter._report_prefix(level_counts, fmt), fmt
        )
        suffix_size = exporter._rendered_size(exporter._report_suffix(fmt), fmt)
        for log in logs:
            if log["level"] == level:
                individual_size = exporter._render_log_block(1, log, fmt, False)[1]
                assert level_prefix_size + individual_size + suffix_size <= threshold
    return threshold


def _expected_size_split_names(fmt, levels, split_by_level):
    parts_per_level = 4 if fmt == "json" else 2
    if split_by_level:
        return [
            f"report_{level}_part{index:03d}.{fmt}"
            for level in levels
            for index in range(1, parts_per_level + 1)
        ]
    return [f"report_part{index:03d}.{fmt}" for index in range(1, parts_per_level + 1)]


def _assert_size_split_records(fmt, summary_path, report_paths, expected, levels):
    from collections import Counter

    counts = dict(Counter(level for level, _message in expected))
    if fmt == "json":
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["split"]["files"] == [str(path) for path in report_paths]
        actual = []
        for report_path in report_paths:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            level = next((item for item in levels if f"_{item}_" in report_path.stem), None)
            assert report["summary"] == ({level: 4} if level else counts)
            actual.extend((result["level"], result["message"]) for result in report["results"])
    elif fmt == "csv":
        summary = summary_path.read_text(encoding="utf-8-sig")
        assert all(path.name in summary for path in report_paths)
        actual = []
        for report_path in report_paths:
            with report_path.open(encoding="utf-8-sig", newline="") as report:
                rows = list(csv.reader(report))
            actual.extend(
                (row[4], row[8])
                for row in rows
                if len(row) == 12 and " boundary-" in row[8]
            )
    else:
        summary = summary_path.read_text(encoding="utf-8")
        assert all(path.name in summary for path in report_paths)
        actual = [
            record
            for report_path in report_paths
            for record in expected
            if f"[{record[0]}] {record[1]}" in report_path.read_text(encoding="utf-8")
        ]
    assert sorted(actual) == sorted(expected)


@pytest.mark.parametrize("fmt", ["csv", "json", "md"])
@pytest.mark.parametrize("split_by_level", [False, True])
def test_size_split_parts_stay_within_limit_and_preserve_records(
    tmp_path, fmt, split_by_level
):
    levels = ("ERROR", "WARN") if split_by_level else ("INFO",)
    logs = [
        _build_level_log(index, level, "payload")
        for level in levels
        for index in range(1, 5)
    ]
    for log in logs:
        log["message"] = f"{log['level']} boundary-{log['line_numbers'][0]}"

    counts = {level: 4 for level in levels}
    # The boundary admits two rendered entries exactly; JSON must reserve its closing suffix.
    max_export_bytes = _size_split_threshold(logs, levels, counts, fmt, split_by_level)

    output_path = tmp_path / f"report.{fmt}"
    files = export_results(
        counts,
        logs,
        str(output_path),
        fmt,
        max_export_bytes=max_export_bytes,
        split_by_level=split_by_level,
    )

    assert Path(files[0]).name == f"report_summary.{fmt}"
    report_paths = [Path(file_path) for file_path in files[1:]]
    assert [path.name for path in report_paths] == _expected_size_split_names(
        fmt, levels, split_by_level
    )
    assert all(path.exists() for path in report_paths)
    for report_path in report_paths:
        assert report_path.stat().st_size <= max_export_bytes

    expected = [(log["level"], log["message"]) for log in logs]
    _assert_size_split_records(fmt, Path(files[0]), report_paths, expected, levels)


def test_export_results_accepts_iterable_input(tmp_path, monkeypatch):
    monkeypatch.setattr("log_analyzer.infrastructure.exporting.exporter.MAX_EXPORT_BYTES_PER_FILE", 5_000)
    output_path = tmp_path / "report.csv"
    payload = "X" * 900

    def log_iter():
        for index in range(1, 4):
            yield _build_log(index, payload)

    files = export_results({"INFO": 3}, log_iter(), str(output_path), "csv")

    assert files == [
        str(tmp_path / "report_summary.csv"),
        str(tmp_path / "report_part001.csv"),
        str(tmp_path / "report_part002.csv"),
    ]
    assert Path(files[1]).exists()
    assert Path(files[2]).exists()


def test_export_results_keeps_single_file_when_level_split_disabled(tmp_path):
    output_path = tmp_path / "report.csv"

    files = export_results(
        {"ERROR": 1, "WARN": 1},
        [_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
        str(output_path),
        "csv",
        split_by_level=False,
    )

    assert files == [str(output_path)]
    text = output_path.read_text(encoding="utf-8-sig")
    assert "ERROR message" in text
    assert "WARN message" in text


def test_export_results_keeps_single_file_when_only_one_level_has_data(tmp_path):
    output_path = tmp_path / "report.csv"

    files = export_results(
        {"ERROR": 2, "WARN": 0},
        [_build_level_log(1, "ERROR"), _build_level_log(2, "ERROR")],
        str(output_path),
        "csv",
        split_by_level=True,
    )

    assert files == [str(output_path)]
    assert output_path.exists()
    assert not (tmp_path / "report_ERROR.csv").exists()


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_export_results_splits_by_level_when_multiple_levels_have_data(tmp_path, fmt, expected_suffix):
    output_path = tmp_path / f"report{expected_suffix}"

    files = export_results(
        {"ERROR": 1, "WARN": 1, "INFO": 0},
        [_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
        str(output_path),
        fmt,
        split_by_level=True,
    )

    expected_files = [
        str(tmp_path / f"report_summary{expected_suffix}"),
        str(tmp_path / f"report_ERROR{expected_suffix}"),
        str(tmp_path / f"report_WARN{expected_suffix}"),
    ]
    assert files == expected_files
    for file_path in files:
        assert Path(file_path).exists()

    error_text = Path(files[1]).read_text(encoding="utf-8-sig" if fmt == "csv" else "utf-8")
    warn_text = Path(files[2]).read_text(encoding="utf-8-sig" if fmt == "csv" else "utf-8")
    assert "ERROR message" in error_text
    assert "WARN message" not in error_text
    assert "WARN message" in warn_text
    assert "ERROR message" not in warn_text

    summary_text = Path(files[0]).read_text(encoding="utf-8-sig" if fmt == "csv" else "utf-8")
    assert "report_ERROR" in summary_text
    assert "report_WARN" in summary_text

    if fmt == "json":
        for file_path in files:
            json.loads(Path(file_path).read_text(encoding="utf-8"))


def test_export_results_splits_by_level_and_size(tmp_path):
    output_path = tmp_path / "report.csv"
    payload = "X" * 900

    files = export_results(
        {"ERROR": 3, "WARN": 1},
        [
            _build_level_log(1, "ERROR", payload),
            _build_level_log(2, "ERROR", payload),
            _build_level_log(3, "ERROR", payload),
            _build_level_log(4, "WARN", "short"),
        ],
        str(output_path),
        "csv",
        max_export_bytes=5_000,
        split_by_level=True,
    )

    assert files == [
        str(tmp_path / "report_summary.csv"),
        str(tmp_path / "report_ERROR_part001.csv"),
        str(tmp_path / "report_ERROR_part002.csv"),
        str(tmp_path / "report_WARN.csv"),
    ]
    for file_path in files:
        assert Path(file_path).exists()


def test_export_results_splits_by_level_accepts_iterable_input(tmp_path):
    output_path = tmp_path / "report.csv"
    yielded = []

    def log_iter():
        for index, level in enumerate(("ERROR", "WARN"), start=1):
            yielded.append(level)
            yield _build_level_log(index, level)

    files = export_results(
        {"ERROR": 1, "WARN": 1},
        log_iter(),
        str(output_path),
        "csv",
        split_by_level=True,
    )

    assert yielded == ["ERROR", "WARN"]
    assert files == [
        str(tmp_path / "report_summary.csv"),
        str(tmp_path / "report_ERROR.csv"),
        str(tmp_path / "report_WARN.csv"),
    ]


def test_export_by_level_context_returns_summary_before_level_files(tmp_path):
    context = exporter._LevelExportContext(
        counts={"ERROR": 1, "WARN": 1},
        matched_logs=[_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
        output=tmp_path / "report.csv",
        format_name="csv",
        threshold=5_000,
        active_levels=["ERROR", "WARN"],
    )

    files = exporter._export_by_level(context)

    assert files == [
        str(tmp_path / "report_summary.csv"),
        str(tmp_path / "report_ERROR.csv"),
        str(tmp_path / "report_WARN.csv"),
    ]
    assert "report_ERROR" in (tmp_path / "report_summary.csv").read_text(encoding="utf-8-sig")
    assert "report_WARN" in (tmp_path / "report_summary.csv").read_text(encoding="utf-8-sig")


def test_export_by_level_context_removes_created_files_when_summary_write_fails(tmp_path, monkeypatch):
    context = exporter._LevelExportContext(
        counts={"ERROR": 1, "WARN": 1},
        matched_logs=[_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
        output=tmp_path / "report.csv",
        format_name="csv",
        threshold=5_000,
        active_levels=["ERROR", "WARN"],
    )

    def fail_summary_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(exporter, "_write_text", fail_summary_write)

    with pytest.raises(OSError, match="disk full"):
        exporter._export_by_level(context)

    assert list(tmp_path.iterdir()) == []


def test_level_writer_construction_failure_closes_earlier_writer_and_cleans_stage(
    tmp_path, monkeypatch
):
    output_path = tmp_path / "report.csv"
    real_open = exporter._open_part_file
    handles = []
    calls = 0

    def fail_later_open(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second writer open failure")
        handle = real_open(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(exporter, "_open_part_file", fail_later_open)

    with pytest.raises(OSError, match="simulated second writer open failure"):
        export_with_options(
            ExportOptions(
                counts={"ERROR": 1, "WARN": 1},
                matched_logs=[_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
                output_path=str(output_path),
                split_by_level=True,
            )
        )

    assert len(handles) == 1
    assert handles[0].closed
    assert list(tmp_path.iterdir()) == []


def test_interrupted_streaming_export_closes_writer_and_cleans_stage(tmp_path, monkeypatch):
    output_path = tmp_path / "report.csv"
    real_open = exporter._open_part_file
    handles = []

    def track_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        handles.append(handle)
        return handle

    def interrupt_render(*_args, **_kwargs):
        raise KeyboardInterrupt("simulated rendering interrupt")

    monkeypatch.setattr(exporter, "_open_part_file", track_open)
    monkeypatch.setattr(exporter, "_render_log_block", interrupt_render)

    with pytest.raises(KeyboardInterrupt, match="simulated rendering interrupt"):
        export_results({"INFO": 1}, [_build_log(1, "payload")], str(output_path))

    assert len(handles) == 1
    assert handles[0].closed
    assert list(tmp_path.iterdir()) == []


def test_cleanup_error_reports_no_publication_and_keeps_render_error_context(
    tmp_path, monkeypatch
):
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    output_path = tmp_path / "report.csv"

    def fail_render(*_args, **_kwargs):
        raise OSError("simulated report render failure")

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("simulated stage cleanup failure")

    monkeypatch.setattr(exporter, "_render_log_block", fail_render)
    monkeypatch.setattr(transaction_module.shutil, "rmtree", fail_cleanup)

    with pytest.raises(OSError) as error:
        export_results({"INFO": 1}, [_build_log(1, "payload")], str(output_path))

    chained = []
    current = error.value
    while current is not None and current not in chained:
        chained.append(current)
        current = current.__cause__ or current.__context__
    assert any("report render failure" in str(item) for item in chained)
    assert "暫存清理失敗" in str(error.value)
    assert "尚無輸出發布" in str(error.value)
    stage_dirs = list(tmp_path.glob(".report.csv.export-*"))
    assert len(stage_dirs) == 1
    assert str(stage_dirs[0].resolve()) in str(error.value)


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_exports_include_cross_file_occurrences(tmp_path, fmt, expected_suffix):
    occurrences = [
        {
            "filename": "logs with spaces/服務.log",
            "line_number": 41,
            "timestamp": "2026-06-07 12:00:01.000",
            "thread": "worker-一",
        },
        {
            "filename": 'archive/"quoted, source".log',
            "line_number": 84,
            "timestamp": "2026-06-07 12:00:02.000",
            "thread": "pool-2",
        },
    ]
    expected_coordinates = [
        "logs with spaces/服務.log:41 | 2026-06-07 12:00:01.000 | worker-一",
        'archive/"quoted, source".log:84 | 2026-06-07 12:00:02.000 | pool-2',
    ]
    log = _build_log(1, "cross-file")
    log.update(
        count=2,
        line_numbers=[41, 84],
        message="group cross-file",
        occurrences=occurrences,
    )
    output_path = tmp_path / f"report{expected_suffix}"

    files = export_results({"INFO": 2}, [log], str(output_path), fmt)

    assert files == [str(output_path)]
    if fmt == "json":
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert len(data["results"]) == 1
        assert data["results"][0]["occurrences"] == occurrences
    elif fmt == "csv":
        with output_path.open(encoding="utf-8-sig", newline="") as report:
            rows = list(csv.reader(report))
        detail_rows = [row for row in rows if len(row) >= 12 and row[8] == log["message"]]
        assert len(detail_rows) == 1
        assert detail_rows[0][-1] == "\n".join(expected_coordinates)
    else:
        report = output_path.read_text(encoding="utf-8")
        assert report.count("### 1. [INFO] group cross-file") == 1
        assert (
            "- **事件座標**:\n  - "
            + expected_coordinates[0]
            + "\n  - "
            + expected_coordinates[1]
            + "\n"
        ) in report


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
@pytest.mark.parametrize("split_by_level", [False, True])
def test_split_exports_keep_all_occurrences(tmp_path, fmt, expected_suffix, split_by_level):
    logs = [
        _build_occurrence_log(1, "ERROR", "x" * 3000),
        _build_occurrence_log(2, "ERROR", "x" * 3000),
        _build_occurrence_log(3, "WARN", "x" * 3000),
        _build_occurrence_log(4, "WARN", "x" * 3000),
    ]
    counts = {"ERROR": 4, "WARN": 4}
    output_path = tmp_path / f"report{expected_suffix}"
    max_export_bytes = 20_000 if split_by_level else 10_000
    expected_coordinates = [
        f"{occurrence['filename']}:{occurrence['line_number']} | "
        f"{occurrence['timestamp']} | {occurrence['thread']}"
        for log in logs
        for occurrence in log["occurrences"]
    ]

    files = export_results(
        counts,
        logs,
        str(output_path),
        fmt,
        max_export_bytes=max_export_bytes,
        split_by_level=split_by_level,
    )

    assert len(files) > 2
    assert Path(files[0]).name == f"report_summary{expected_suffix}"
    if split_by_level:
        assert [Path(file_path).name for file_path in files[1:]] == [
            f"report_ERROR{expected_suffix}",
            f"report_WARN{expected_suffix}",
        ]
    else:
        assert all("_part" in Path(file_path).stem for file_path in files[1:])

    actual_coordinates = [
        coordinate
        for file_path in files[1:]
        for coordinate in _read_exported_coordinates(Path(file_path), fmt)
    ]
    assert len(actual_coordinates) == sum(log["count"] for log in logs)
    assert len(actual_coordinates) == len(set(actual_coordinates))
    assert sorted(actual_coordinates) == sorted(expected_coordinates)


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_legacy_rows_without_occurrences_still_export(tmp_path, fmt, expected_suffix):
    log = _build_log(1, "legacy")
    output_path = tmp_path / f"report{expected_suffix}"

    files = export_results({"INFO": 1}, [log], str(output_path), fmt)

    assert files == [str(output_path)]
    if fmt == "json":
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["results"][0]["occurrences"] == []
    elif fmt == "csv":
        with output_path.open(encoding="utf-8-sig", newline="") as report:
            rows = list(csv.reader(report))
        detail_rows = [row for row in rows if len(row) >= 12 and row[8] == log["message"]]
        assert len(detail_rows) == 1
        assert detail_rows[0][-1] == ""
    else:
        report = output_path.read_text(encoding="utf-8")
        assert "- **事件座標**:\n" in report
        assert _read_exported_coordinates(output_path, fmt) == []


_SELECTED_DETAIL_FIELDS = (
    "occurrences",
    "stacktrace",
    "message_body",
    "filename",
    "logger",
    "thread",
    "last_timestamp",
    "timestamp",
    "line_numbers",
    "count",
)
_SELECTED_DETAIL_KEYS_IN_ORDER = [
    "count",
    "line_numbers",
    "timestamp",
    "last_timestamp",
    "thread",
    "logger",
    "filename",
    "message_body",
    "stacktrace",
    "occurrences",
]
_SELECTED_DETAIL_LABELS_IN_ORDER = [
    "出現次數",
    "行號",
    "首次時間",
    "最後時間",
    "執行緒",
    "記錄器",
    "檔案名稱",
    "訊息本文",
    "堆疊追蹤",
    "發生位置",
]


def _build_selectable_log() -> dict:
    log = _build_occurrence_log(1, "INFO", "BODY-KEEP")
    log["occurrences"][0]["filename"] = "source-1.log"
    log.update(
        level="LEVEL-OMITTED",
        message="MESSAGE-OMITTED",
        last_timestamp="2026-06-07 12:00:59.000",
        stacktrace="STACKTRACE-KEEP",
    )
    return log


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_selected_export_fields_keep_only_requested_details_in_canonical_order(
    tmp_path, fmt, expected_suffix
):
    output_path = tmp_path / f"selected{expected_suffix}"
    log = _build_selectable_log()

    if fmt == "csv":
        files = export_with_options(
            ExportOptions(
                counts={"INFO": 2},
                matched_logs=[log],
                output_path=str(output_path),
                format=fmt,
                fields=_SELECTED_DETAIL_FIELDS,
            )
        )
    else:
        files = export_results(
            {"INFO": 2},
            [log],
            str(output_path),
            fmt,
            fields=_SELECTED_DETAIL_FIELDS,
        )

    assert files == [str(output_path)]
    if fmt == "csv":
        with output_path.open(encoding="utf-8-sig", newline="") as report:
            rows = list(csv.reader(report))
        header_index = rows.index(_SELECTED_DETAIL_LABELS_IN_ORDER)
        header = rows[header_index]
        row = rows[header_index + 1]

        assert header == _SELECTED_DETAIL_LABELS_IN_ORDER
        assert len(row) == len(header)
        assert row == [
            "2",
            "11, 12",
            "2026-06-07 12:00:01.000",
            "2026-06-07 12:00:59.000",
            "main",
            "com.test.App",
            "app.log",
            "BODY-KEEP",
            "STACKTRACE-KEEP",
            "source-1.log:11 | 2026-06-07 12:00:01.000 | worker-1-a\n"
            'archive/1-"quoted, source".log:12 | 2026-06-07 12:01:01.000 | worker-1-b',
        ]
        assert "LEVEL-OMITTED" not in row
        assert "MESSAGE-OMITTED" not in row
    elif fmt == "json":
        data = json.loads(output_path.read_text(encoding="utf-8"))
        result = data["results"][0]

        assert data["summary"] == {"INFO": 2}
        assert list(result) == _SELECTED_DETAIL_KEYS_IN_ORDER
        assert result["count"] == 2
        assert result["line_numbers"] == [11, 12]
        assert result["timestamp"] == "2026-06-07 12:00:01.000"
        assert result["last_timestamp"] == "2026-06-07 12:00:59.000"
        assert result["thread"] == "main"
        assert result["logger"] == "com.test.App"
        assert result["filename"] == "app.log"
        assert result["message_body"] == "BODY-KEEP"
        assert result["stacktrace"] == "STACKTRACE-KEEP"
        assert result["occurrences"] == log["occurrences"]
        assert "level" not in result
        assert "message" not in result
    else:
        report = output_path.read_text(encoding="utf-8")
        heading = next(line for line in report.splitlines() if line.startswith("### "))

        assert heading == "### 1. 記錄"
        assert "LEVEL-OMITTED" not in heading
        assert "MESSAGE-OMITTED" not in report
        for expected_value in (
            "出現次數",
            "2",
            "11, 12",
            "2026-06-07 12:00:01.000",
            "2026-06-07 12:00:59.000",
            "main",
            "com.test.App",
            "app.log",
            "BODY-KEEP",
            "STACKTRACE-KEEP",
            "source-1.log:11",
            'archive/1-"quoted, source".log:12',
        ):
            assert expected_value in report


@pytest.mark.parametrize("fields", [(), ("unknown_field",)])
def test_invalid_selected_fields_fail_before_split_export_creates_files(tmp_path, fields):
    output_path = tmp_path / "report.csv"

    with pytest.raises(ValueError):
        export_results(
            {"ERROR": 1, "WARN": 1},
            [_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
            str(output_path),
            split_by_level=True,
            fields=fields,
        )

    assert list(tmp_path.iterdir()) == []


def test_selected_fields_flow_through_level_and_size_split_writers(tmp_path):
    output_path = tmp_path / "split.csv"
    logs = [
        _build_level_log(1, "ERROR", "excluded"),
        _build_level_log(2, "ERROR", "excluded"),
        _build_level_log(3, "ERROR", "excluded"),
        _build_level_log(4, "WARN", "excluded"),
    ]
    for index, log in enumerate(logs, start=1):
        log["message_body"] = f"BODY-{index}-" + "X" * 900
        log["message"] = f"MESSAGE-EXCLUDED-{index}"

    files = export_results(
        {"ERROR": 3, "WARN": 1},
        iter(logs),
        str(output_path),
        max_export_bytes=1_500,
        split_by_level=True,
        fields=("message_body", "thread"),
    )

    assert files[0] == str(tmp_path / "split_summary.csv")
    error_files = [Path(file_path) for file_path in files if "_ERROR" in Path(file_path).name]
    warn_files = [Path(file_path) for file_path in files if "_WARN" in Path(file_path).name]
    assert len(error_files) >= 2
    assert len(warn_files) == 1
    for file_path in [*error_files, *warn_files]:
        with file_path.open(encoding="utf-8-sig", newline="") as report:
            rows = list(csv.reader(report))
        header_index = rows.index(["執行緒", "訊息本文"])
        assert len(rows[header_index + 1]) == 2
        text = file_path.read_text(encoding="utf-8-sig")
        assert "MESSAGE-EXCLUDED" not in text
        assert "ERROR message" not in text
        assert "WARN message" not in text
        assert "BODY-" in text


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_default_export_preserves_all_detail_fields_and_legacy_layout(
    tmp_path, fmt, expected_suffix
):
    output_path = tmp_path / f"default{expected_suffix}"
    log = _build_log(1, "BODY-DEFAULT")

    files = export_results({"INFO": 1}, [log], str(output_path), fmt)

    assert files == [str(output_path)]
    if fmt == "csv":
        with output_path.open(encoding="utf-8-sig", newline="") as report:
            rows = list(csv.reader(report))
        detail_row = next(row for row in rows if len(row) == 12 and row[8] == log["message"])
        assert detail_row == [
            "1",
            "1",
            "2026-06-07 12:00:01.000",
            "2026-06-07 12:00:01.000",
            "INFO",
            "main",
            "com.test.App",
            "app.log",
            "Message 1 BODY-DEFAULT",
            "Body 1 BODY-DEFAULT",
            "",
            "",
        ]
    elif fmt == "json":
        result = json.loads(output_path.read_text(encoding="utf-8"))["results"][0]
        assert list(result) == [
            "count",
            "line_numbers",
            "timestamp",
            "last_timestamp",
            "level",
            "thread",
            "logger",
            "filename",
            "message",
            "message_body",
            "stacktrace",
            "occurrences",
        ]
        assert result["occurrences"] == []
    else:
        report = output_path.read_text(encoding="utf-8")
        assert "### 1. [INFO] Message 1 BODY-DEFAULT" in report
        assert "Body 1 BODY-DEFAULT" in report


def test_export_field_normalization_returns_unique_canonical_selection():
    from log_analyzer.infrastructure.exporting.fields import (
        EXPORT_FIELDS,
        EXPORT_FIELD_LABELS,
        normalize_export_fields,
    )

    assert EXPORT_FIELDS == (
        "count",
        "line_numbers",
        "timestamp",
        "last_timestamp",
        "level",
        "thread",
        "logger",
        "filename",
        "message",
        "message_body",
        "stacktrace",
        "occurrences",
    )
    assert list(EXPORT_FIELD_LABELS) == list(EXPORT_FIELDS)
    assert EXPORT_FIELD_LABELS["message_body"] == "訊息本文"
    assert normalize_export_fields(None) == EXPORT_FIELDS
    assert normalize_export_fields(("message", "count", "timestamp", "count")) == (
        "count",
        "timestamp",
        "message",
    )
    with pytest.raises(ValueError):
        normalize_export_fields(())
    with pytest.raises(ValueError):
        normalize_export_fields(("not_a_field",))


@pytest.mark.parametrize("fmt", ["csv", "json", "md"])
def test_export_refuses_existing_target_without_overwrite(tmp_path, fmt):
    output_path = tmp_path / f"report.{fmt}"
    output_path.write_bytes(b"keep existing report")

    with pytest.raises(ExportConflictError) as error:
        export_results({"INFO": 1}, [_build_log(1, "new")], str(output_path), fmt)

    assert error.value.paths == (str(output_path.resolve()),)
    assert output_path.read_bytes() == b"keep existing report"
    assert list(tmp_path.iterdir()) == [output_path]


@pytest.mark.parametrize(
    "target_name, split_by_level, max_export_bytes",
    [
        ("report_summary.csv", True, None),
        ("report_WARN.csv", True, None),
        ("report_part001.csv", False, 1),
    ],
)
def test_export_refuses_summary_and_split_part_collisions(
    tmp_path, target_name, split_by_level, max_export_bytes
):
    output_path = tmp_path / "report.csv"
    existing = tmp_path / target_name
    existing.write_bytes(b"existing output")
    logs = [_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")]

    with pytest.raises(ExportConflictError) as error:
        export_results(
            {"ERROR": 1, "WARN": 1},
            logs,
            str(output_path),
            "csv",
            max_export_bytes=max_export_bytes,
            split_by_level=split_by_level,
        )

    assert error.value.paths == (str(existing.resolve()),)
    assert existing.read_bytes() == b"existing output"
    assert {path.name for path in tmp_path.iterdir()} == {target_name}


@pytest.mark.parametrize("fmt", ["csv", "json", "md"])
def test_export_results_can_explicitly_overwrite_existing_target(tmp_path, fmt):
    output_path = tmp_path / f"report.{fmt}"
    output_path.write_text("old report", encoding="utf-8")

    files = export_results(
        {"INFO": 1},
        [_build_log(1, "new")],
        str(output_path),
        fmt,
        overwrite=True,
    )

    assert files == [str(output_path)]
    assert output_path.read_text(encoding="utf-8-sig" if fmt == "csv" else "utf-8") != "old report"
    assert list(tmp_path.iterdir()) == [output_path]


@pytest.mark.parametrize("fmt", ["csv", "json", "md"])
def test_split_summary_references_final_paths_and_never_staging_paths(tmp_path, fmt):
    output_path = tmp_path / f"report.{fmt}"

    files = export_results(
        {"ERROR": 1, "WARN": 1},
        [_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
        str(output_path),
        fmt,
        split_by_level=True,
    )

    summary = Path(files[0]).read_text(encoding="utf-8-sig" if fmt == "csv" else "utf-8")
    if fmt == "json":
        assert json.loads(summary)["split"]["files"] == files[1:]
    else:
        assert all(str(Path(path).resolve()) in summary for path in files[1:])
    assert ".export-" not in summary
    assert not any(path.name.startswith(".") for path in tmp_path.iterdir())


def test_export_render_failure_preserves_old_split_files(tmp_path, monkeypatch):
    output_path = tmp_path / "report.csv"
    old_files = {
        tmp_path / "report_summary.csv": b"old summary",
        tmp_path / "report_ERROR.csv": b"old error",
        tmp_path / "report_WARN.csv": b"old warn",
    }
    for path, content in old_files.items():
        path.write_bytes(content)

    def fail_summary(*_args, **_kwargs):
        raise OSError("simulated summary write failure")

    monkeypatch.setattr(exporter, "_write_text", fail_summary)

    with pytest.raises(OSError, match="simulated summary write failure"):
        export_results(
            {"ERROR": 1, "WARN": 1},
            [_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
            str(output_path),
            "csv",
            split_by_level=True,
            overwrite=True,
        )

    assert {path: path.read_bytes() for path in old_files} == old_files
    assert set(tmp_path.iterdir()) == set(old_files)


@pytest.mark.parametrize("failure_point", ["write", "close", "render"])
def test_export_failures_preserve_old_split_family(tmp_path, monkeypatch, failure_point):
    output_path = tmp_path / "report.csv"
    old_files = {
        tmp_path / "report_summary.csv": b"old summary",
        tmp_path / "report_ERROR.csv": b"old error",
        tmp_path / "report_WARN.csv": b"old warn",
    }
    for path, content in old_files.items():
        path.write_bytes(content)

    if failure_point == "write":
        def fail_write(*_args, **_kwargs):
            raise OSError("simulated write failure")

        monkeypatch.setattr(exporter, "_render_log_block", fail_write)
    elif failure_point == "close":
        def fail_close(handle, _format_name):
            handle.close()
            raise OSError("simulated close failure")

        monkeypatch.setattr(exporter, "_close_part_file", fail_close)
    else:
        def fail_render(*_args, **_kwargs):
            raise OSError("simulated summary render failure")

        monkeypatch.setattr(exporter, "_render_summary", fail_render)

    with pytest.raises(OSError, match="simulated"):
        export_results(
            {"ERROR": 1, "WARN": 1},
            [_build_level_log(1, "ERROR"), _build_level_log(2, "WARN")],
            str(output_path),
            "csv",
            split_by_level=True,
            overwrite=True,
        )

    assert {path: path.read_bytes() for path in old_files} == old_files
    assert set(tmp_path.iterdir()) == set(old_files)
