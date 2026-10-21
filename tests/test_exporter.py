from pathlib import Path
import csv
import json

import pytest

from log_analyzer.infrastructure.exporter import ExportOptions, export_results, export_with_options
from log_analyzer.infrastructure import exporter


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
    monkeypatch.setattr("log_analyzer.infrastructure.exporter.MAX_EXPORT_BYTES_PER_FILE", 10_000)
    output_path = tmp_path / f"report{expected_suffix}"

    files = export_results({"INFO": 1}, [_build_log(1, "short")], str(output_path), fmt)

    assert files == [str(output_path)]
    assert output_path.exists()


@pytest.mark.parametrize("fmt, expected_suffix", [("csv", ".csv"), ("json", ".json"), ("md", ".md")])
def test_export_results_splits_large_reports(tmp_path, monkeypatch, fmt, expected_suffix):
    monkeypatch.setattr("log_analyzer.infrastructure.exporter.MAX_EXPORT_BYTES_PER_FILE", 5_000)
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


def test_export_results_accepts_iterable_input(tmp_path, monkeypatch):
    monkeypatch.setattr("log_analyzer.infrastructure.exporter.MAX_EXPORT_BYTES_PER_FILE", 5_000)
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
