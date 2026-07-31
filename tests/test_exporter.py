from pathlib import Path
import json

import pytest

from log_analyzer.infrastructure.exporter import export_results
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
