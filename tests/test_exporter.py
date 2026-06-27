from pathlib import Path
import json

import pytest

from log_analyzer.infrastructure.exporter import export_results


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
