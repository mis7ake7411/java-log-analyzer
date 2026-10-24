"""Exercise advanced CLI behavior through the extracted frozen executable."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import uuid


_MAX_PART_BYTES = 1024 * 1024


def _runtime_path(target, path: Path) -> str:
    relative = path.relative_to(target.data).as_posix()
    return target.path(relative)


def _run_cli(target, source: Path, output: Path, *options, expect_success=True):
    return target.run(
        [
            target.executable,
            _runtime_path(target, source),
            "-o",
            _runtime_path(target, output),
            *options,
        ],
        cwd="work A",
        expect_success=expect_success,
    )


def _entry(timestamp: str, level: str, message: str, thread="main") -> str:
    return f"{timestamp} [{thread}] {level:<5} com.test.App - {message}\n"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _csv_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as report:
        return list(csv.reader(report))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_xml(path: Path) -> None:
    _write(
        path,
        """<configuration>
    <property name="CONSOLE_LOG_PATTERN" value="[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n"/>
    <property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/>
</configuration>
""",
    )


def _verify_xml_selection(root: Path, target) -> str:
    xml_case = root / "xml-selection"
    source = xml_case / "chosen.log"
    _write(source, "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: selected-only-marker\n")
    _write(
        xml_case / "sibling.txt",
        "".join(
            f"[2026-06-06 10:00:00.00{index}] [INFO ] [main] "
            f"com.test.App.run({index}) - sibling-only-marker\n"
            for index in range(3)
        ),
    )
    xml = xml_case / "logback.xml"
    _write_xml(xml)
    report = xml_case / "report.json"
    completed = _run_cli(target, source, report, "--logback-xml", _runtime_path(target, xml), "-f", "json")
    data = _json(report)
    _assert_xml_selection(data, completed.stdout, report)
    return "XML automatic pattern selection and single-file scope"


def _assert_xml_selection(data, stdout: str, report: Path) -> None:
    assert "FILE_LOG_PATTERN" in stdout
    assert data["summary"] == {"INFO": 1}
    assert len(data["results"]) == 1
    selected = data["results"][0]
    assert selected["message"] == "selected-only-marker"
    assert selected["filename"] == "chosen.log"
    assert selected["line_numbers"] == [1]
    assert "sibling-only-marker" not in report.read_text(encoding="utf-8")


def _verify_xml_manual_override(root: Path, target) -> str:
    override_case = root / "xml-manual-override"
    override_source = override_case / "mixed.log"
    _write(
        override_source,
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: xml-only-marker\n"
        + _entry("2026-05-25 15:51:33.198", "WARN", "manual-only-marker"),
    )
    override_xml = override_case / "logback.xml"
    _write_xml(override_xml)
    override_report = override_case / "report.json"
    completed = _run_cli(
        target,
        override_source,
        override_report,
        "--logback-xml",
        _runtime_path(target, override_xml),
        "--pattern",
        "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n",
        "-f",
        "json",
    )
    data = _json(override_report)
    assert completed.returncode == 0
    assert data["summary"] == {"WARN": 1}
    assert [entry["message"] for entry in data["results"]] == ["manual-only-marker"]
    return "Manual pattern takes precedence over XML"


def _filter_fixture(case: Path) -> Path:
    source = case / "filters.log"
    _write(
        source,
        _entry("2026-06-07 10:00:00.000", "ERROR", "KeywordCase start-outside")
        + _entry("2026-06-07 10:01:00.000", "ERROR", "KeywordCase boundary-start")
        + _entry("2026-06-07 10:02:00.000", "WARN", "keywordcase middle-lower")
        + _entry("2026-06-07 10:03:00.000", "INFO", "no-keyword middle")
        + _entry("2026-06-07 10:04:00.000", "DEBUG", "keywordcase boundary-end")
        + _entry("2026-06-07 10:05:00.000", "TRACE", "KeywordCase end-outside"),
    )
    return source


def _verify_date_bounds(case: Path, target, source: Path) -> None:
    bounded_report = case / "bounded.json"
    _run_cli(
        target,
        source,
        bounded_report,
        "-f",
        "json",
        "--level",
        "ALL",
        "--start",
        "2026-06-07 10:01:00",
        "--end",
        "2026-06-07 10:04:00",
    )
    bounded = _json(bounded_report)
    assert bounded["summary"] == {"ERROR": 1, "WARN": 1, "INFO": 1, "DEBUG": 1}
    assert [entry["message"] for entry in bounded["results"]] == [
        "KeywordCase boundary-start",
        "keywordcase middle-lower",
        "no-keyword middle",
        "keywordcase boundary-end",
    ]
    assert bounded["results"][0]["timestamp"] == "2026-06-07 10:01:00.000"
    assert bounded["results"][-1]["timestamp"] == "2026-06-07 10:04:00.000"

def _verify_keyword_case(case: Path, target, source: Path) -> None:
    sensitive_report = case / "case-sensitive.json"
    _run_cli(target, source, sensitive_report, "-f", "json", "--level", "ALL", "-k", "KeywordCase")
    sensitive = _json(sensitive_report)
    assert sensitive["summary"] == {"ERROR": 2, "TRACE": 1}
    assert [entry["message"] for entry in sensitive["results"]] == [
        "KeywordCase start-outside",
        "KeywordCase boundary-start",
        "KeywordCase end-outside",
    ]

    insensitive_report = case / "case-insensitive.json"
    _run_cli(target, source, insensitive_report, "-f", "json", "--level", "ALL", "-k", "KeywordCase", "-i")
    insensitive = _json(insensitive_report)
    assert insensitive["summary"] == {"ERROR": 2, "WARN": 1, "DEBUG": 1, "TRACE": 1}
    assert len(insensitive["results"]) == 5
    assert all("keywordcase" in entry["message"].lower() for entry in insensitive["results"])


def _verify_repeated_levels(case: Path, target, source: Path) -> None:
    multiple_report = case / "multiple-levels.json"
    _run_cli(target, source, multiple_report, "-f", "json", "--level", "ERROR", "--level", "WARN")
    multiple = _json(multiple_report)
    assert multiple["summary"] == {"ERROR": 2, "WARN": 1}
    assert {entry["level"] for entry in multiple["results"]} == {"ERROR", "WARN"}


def _verify_sort_modes(case: Path, target) -> None:
    sort_source = case / "sort.log"
    _write(
        sort_source,
        _entry("2026-06-07 10:03:00.000", "DEBUG", "Debug later")
        + _entry("2026-06-07 10:02:00.000", "ERROR", "Error later")
        + _entry("2026-06-07 10:01:00.000", "WARN", "Warn later")
        + _entry("2026-06-07 10:00:00.000", "INFO", "Info first"),
    )
    time_report = case / "sort-time.json"
    _run_cli(target, sort_source, time_report, "-f", "json", "--level", "ALL", "--sort", "time")
    time_data = _json(time_report)
    assert [entry["message"] for entry in time_data["results"]] == [
        "Info first",
        "Warn later",
        "Error later",
        "Debug later",
    ]
    level_report = case / "sort-level.json"
    _run_cli(target, sort_source, level_report, "-f", "json", "--level", "ALL", "--sort", "level")
    level_data = _json(level_report)
    assert [entry["message"] for entry in level_data["results"]] == [
        "Error later",
        "Warn later",
        "Info first",
        "Debug later",
    ]


def _verify_filters_and_sorting(root: Path, target) -> str:
    case = root / "filters"
    source = _filter_fixture(case)
    _verify_date_bounds(case, target, source)
    _verify_keyword_case(case, target, source)
    _verify_repeated_levels(case, target, source)
    _verify_sort_modes(case, target)
    return "Keyword sensitivity, inclusive time bounds, repeated levels, ALL and sort order"


def _verify_multiline_occurrences(root: Path, target) -> str:
    case = root / "multiline"
    source = case / "trace.log"
    trace = "java.lang.RuntimeException: Trace marker\n    at com.test.App.main(App.java:7)\n"
    _write(
        source,
        _entry("2026-06-07 12:00:01.000", "ERROR", "Repeated failure", "worker-1")
        + trace
        + _entry("2026-06-07 12:00:02.000", "ERROR", "Repeated failure", "worker-2")
        + trace,
    )
    report = case / "report.json"
    _run_cli(target, source, report, "-f", "json")
    data = _json(report)
    _assert_multiline_result(data)
    return "Multiline stacktraces remain distinct with exact physical line coordinates"


def _assert_multiline_result(data) -> None:
    assert data["summary"] == {"ERROR": 2}
    assert len(data["results"]) == 2
    results = data["results"]
    _assert_multiline_fields(results)
    _assert_multiline_coordinates(results)


def _assert_multiline_fields(results) -> None:
    assert [result["message"] for result in results] == ["Repeated failure", "Repeated failure"]
    assert [result["count"] for result in results] == [1, 1]
    assert [result["line_numbers"] for result in results] == [[1], [4]]
    assert [result["filename"] for result in results] == ["trace.log", "trace.log"]
    assert [result["timestamp"] for result in results] == [
        "2026-06-07 12:00:01.000",
        "2026-06-07 12:00:02.000",
    ]
    assert [result["last_timestamp"] for result in results] == [
        "2026-06-07 12:00:01.000",
        "2026-06-07 12:00:02.000",
    ]
    assert [result["stacktrace"] for result in results] == [
        "2: java.lang.RuntimeException: Trace marker\n"
        "    3:     at com.test.App.main(App.java:7)",
        "5: java.lang.RuntimeException: Trace marker\n"
        "    6:     at com.test.App.main(App.java:7)",
    ]


def _assert_multiline_coordinates(results) -> None:
    assert [result["occurrences"] for result in results] == [
        [{
            "filename": "trace.log",
            "line_number": 1,
            "timestamp": "2026-06-07 12:00:01.000",
            "thread": "worker-1",
        }],
        [{
            "filename": "trace.log",
            "line_number": 4,
            "timestamp": "2026-06-07 12:00:02.000",
            "thread": "worker-2",
        }],
    ]


def _verify_plain_duplicate_occurrences(root: Path, target) -> str:
    case = root / "plain-duplicates"
    source = case / "duplicate.log"
    _write(
        source,
        _entry("2026-06-07 12:05:01.000", "ERROR", "Same plain message", "worker-1")
        + _entry("2026-06-07 12:05:02.000", "ERROR", "Same plain message", "worker-2"),
    )
    report = case / "report.json"
    _run_cli(target, source, report, "-f", "json")
    data = _json(report)
    _assert_plain_duplicate_result(data)
    return "Repeated plain messages merge count and retain occurrence coordinates"


def _assert_plain_duplicate_result(data) -> None:
    assert data["summary"] == {"ERROR": 2}
    assert len(data["results"]) == 1
    result = data["results"][0]
    assert result["message"] == "Same plain message"
    assert result["count"] == 2
    assert result["filename"] == "duplicate.log"
    assert result["line_numbers"] == [1, 2]
    assert result["timestamp"] == "2026-06-07 12:05:01.000"
    assert result["last_timestamp"] == "2026-06-07 12:05:02.000"
    assert result["message_body"] == ""
    assert result["stacktrace"] == ""
    assert result["occurrences"] == [
        {
            "filename": "duplicate.log",
            "line_number": 1,
            "timestamp": "2026-06-07 12:05:01.000",
            "thread": "worker-1",
        },
        {
            "filename": "duplicate.log",
            "line_number": 2,
            "timestamp": "2026-06-07 12:05:02.000",
            "thread": "worker-2",
        },
    ]


def _verify_one_level_split(case: Path, target, source: Path, fmt: str) -> None:
    output_dir = case / fmt
    output_dir.mkdir()
    output = output_dir / f"report.{fmt}"
    _run_cli(target, source.parent, output, "-f", fmt, "--level", "ALL", "--split-by-level")
    summary = output_dir / f"report_summary.{fmt}"
    error_file = output_dir / f"report_ERROR.{fmt}"
    warn_file = output_dir / f"report_WARN.{fmt}"
    assert summary.is_file() and error_file.is_file() and warn_file.is_file()
    assert not output.exists()
    assert set(output_dir.iterdir()) == {summary, error_file, warn_file}
    _assert_marker_content(error_file, "ERROR-marker-alpha", "WARN-marker-beta")
    _assert_marker_content(warn_file, "WARN-marker-beta", "ERROR-marker-alpha")
    if fmt == "json":
        _assert_json_level_split(summary, error_file, warn_file)
    else:
        _assert_text_level_split(summary, error_file, warn_file, fmt)


def _assert_json_level_split(summary: Path, error_file: Path, warn_file: Path) -> None:
    summary_data = _json(summary)
    assert summary_data["summary"] == {"ERROR": 1, "WARN": 1}
    assert summary_data["results"] == []
    assert summary_data["split"]["enabled"] is True
    assert _split_basenames(summary_data["split"]["files"]) == {
        error_file.name,
        warn_file.name,
    }
    assert _json(error_file)["summary"] == {"ERROR": 1}
    assert [entry["message"] for entry in _json(error_file)["results"]] == [
        "ERROR-marker-alpha"
    ]
    assert _json(warn_file)["summary"] == {"WARN": 1}
    assert [entry["message"] for entry in _json(warn_file)["results"]] == [
        "WARN-marker-beta"
    ]


def _assert_text_level_split(summary: Path, error_file: Path, warn_file: Path, fmt: str) -> None:
    if fmt == "csv":
        summary_rows = _csv_rows(summary)
        assert ["ERROR", "1"] in summary_rows
        assert ["WARN", "1"] in summary_rows
        summary_text = summary.read_text(encoding="utf-8-sig")
    else:
        summary_text = summary.read_text(encoding="utf-8")
    assert error_file.name in summary_text
    assert warn_file.name in summary_text


def _verify_level_split_formats(root: Path, target) -> str:
    case = root / "level-splits"
    source = case / "logs" / "sample.log"
    _write(
        source,
        _entry("2026-06-07 12:00:00.000", "ERROR", "ERROR-marker-alpha")
        + _entry("2026-06-07 12:00:01.000", "WARN", "WARN-marker-beta"),
    )
    for fmt in ("csv", "json", "md"):
        _verify_one_level_split(case, target, source, fmt)
    return "Level-split CSV, JSON and Markdown report contents"


def _assert_marker_content(path: Path, present: str, absent: str) -> None:
    text = path.read_text(encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8")
    assert text.count(present) == 1
    assert absent not in text


def _split_basenames(files) -> set[str]:
    return {Path(file_path.replace("\\", "/")).name for file_path in files}


def _bulk_fixture(root: Path, count=2500) -> Path:
    logs = root / "bulk-logs"
    logs.mkdir(parents=True)
    source = logs / "bulk.log"
    with source.open("w", encoding="utf-8", newline="\n") as log_file:
        for index in range(count):
            log_file.write(
                _entry(
                    "2026-10-24 12:00:00.000",
                    "ERROR",
                    f"bulk-error-{index:04d} {'x' * 1000}",
                    f"worker-{index % 8}",
                )
            )
        for index in range(3):
            log_file.write(
                _entry(
                    f"2026-10-24 12:01:0{index}.000",
                    "WARN",
                    f"warn-marker-{index:03d}",
                    f"warning-{index}",
                )
            )
    return logs


def _verify_csv_size_split(root: Path, target, source: Path) -> str:
    output_dir = root / "csv-size-split"
    output_dir.mkdir()
    output = output_dir / "report.csv"
    _run_cli(target, source, output, "-f", "csv", "--level", "ALL", "--max-export-mb", "1")
    summary = output_dir / "report_summary.csv"
    parts = sorted(output_dir.glob("report_part*.csv"))
    _assert_csv_split_family(output, summary, parts)
    _assert_csv_split_summary(summary, parts)
    _assert_text_split_data(parts)
    return "1 MB CSV size split preserves all 2503 records and summary references"


def _assert_csv_split_family(output: Path, summary: Path, parts: list[Path]) -> None:
    assert summary.is_file()
    assert len(parts) >= 3
    assert [part.name for part in parts] == [f"report_part{index:03d}.csv" for index in range(1, len(parts) + 1)]
    assert not output.exists()
    assert set(output.parent.glob("report*.csv")) == {summary, *parts}
    assert all(part.stat().st_size <= _MAX_PART_BYTES for part in parts)


def _assert_csv_split_summary(summary: Path, parts: list[Path]) -> None:
    summary_rows = _csv_rows(summary)
    assert ["ERROR", "2500"] in summary_rows
    assert ["WARN", "3"] in summary_rows
    assert all(part.name in summary.read_text(encoding="utf-8-sig") for part in parts)


def _assert_text_split_data(parts: list[Path]) -> None:
    report_text = "\n".join(part.read_text(encoding="utf-8-sig") for part in parts)
    error_ids = re.findall(r"bulk-error-(\d{4})", report_text)
    warn_ids = re.findall(r"warn-marker-(\d{3})", report_text)
    assert len(error_ids) == 2500
    assert set(error_ids) == {f"{index:04d}" for index in range(2500)}
    assert warn_ids == ["000", "001", "002"]


def _verify_markdown_size_split(root: Path, target, source: Path) -> str:
    output_dir = root / "markdown-size-split"
    output_dir.mkdir()
    output = output_dir / "report.md"
    _run_cli(target, source, output, "-f", "md", "--level", "ALL", "--max-export-mb", "1")
    summary = output_dir / "report_summary.md"
    parts = sorted(output_dir.glob("report_part*.md"))
    assert len(parts) >= 3
    assert [part.name for part in parts] == [f"report_part{index:03d}.md" for index in range(1, len(parts) + 1)]
    assert set(output_dir.iterdir()) == {summary, *parts}
    assert all(part.stat().st_size <= _MAX_PART_BYTES for part in parts)
    assert all(part.name in summary.read_text(encoding="utf-8") for part in parts)
    _assert_text_split_data(parts)
    return "1 MB Markdown size split preserves all 2503 records and summary references"


def _verify_combined_json_split(root: Path, target, source: Path) -> str:
    output_dir = root / "combined-json-split"
    output_dir.mkdir()
    output = output_dir / "report.json"
    _run_cli(
        target,
        source,
        output,
        "-f",
        "json",
        "--level",
        "ALL",
        "--split-by-level",
        "--max-export-mb",
        "1",
    )
    summary = _json(output_dir / "report_summary.json")
    error_entries, warning_entries = _combined_json_entries(output_dir, summary)
    _assert_combined_json_content(summary, error_entries, warning_entries)
    return "Combined 1 MB JSON level/size split preserves all records and summary family"


def _combined_json_entries(output_dir: Path, summary) -> tuple[list[dict], list[dict]]:
    assert summary["summary"] == {"ERROR": 2500, "WARN": 3}
    output_files = [path for path in output_dir.iterdir() if path.name != "report_summary.json"]
    error_parts = sorted(output_dir.glob("report_ERROR_part*.json"))
    warn_file = output_dir / "report_WARN.json"
    _assert_combined_json_family(summary, output_files, error_parts, warn_file)
    error_entries = _read_error_parts(error_parts)
    warning_data = _json(warn_file)
    assert warning_data["summary"] == {"WARN": 3}
    return error_entries, warning_data["results"]


def _assert_combined_json_family(summary, output_files, error_parts, warn_file: Path) -> None:
    assert len(error_parts) >= 3
    assert warn_file.is_file()
    assert [path.name for path in error_parts] == [
        f"report_ERROR_part{index:03d}.json" for index in range(1, len(error_parts) + 1)
    ]
    assert {path.name for path in output_files} == {
        *(path.name for path in error_parts),
        warn_file.name,
    }
    assert not (warn_file.parent / "report.json").exists()
    assert not (warn_file.parent / "report_ERROR.json").exists()
    assert _split_basenames(summary["split"]["files"]) == {path.name for path in output_files}
    assert all(path.stat().st_size <= _MAX_PART_BYTES for path in error_parts)


def _read_error_parts(error_parts: list[Path]) -> list[dict]:
    error_entries = []
    for part in error_parts:
        data = _json(part)
        assert data["summary"] == {"ERROR": 2500}
        error_entries.extend(data["results"])
    return error_entries


def _assert_combined_json_content(summary, error_entries: list[dict], warning_entries: list[dict]) -> None:
    assert summary["results"] == []
    assert summary["split"]["enabled"] is True
    assert len(error_entries) == 2500
    assert len(warning_entries) == 3
    assert all(entry["level"] == "ERROR" for entry in error_entries)
    assert all(entry["level"] == "WARN" for entry in warning_entries)
    error_ids = [re.search(r"bulk-error-(\d{4})", entry["message"]).group(1) for entry in error_entries]
    assert len(set(error_ids)) == 2500
    assert set(error_ids) == {f"{index:04d}" for index in range(2500)}
    assert [entry["message"] for entry in warning_entries] == [
        "warn-marker-000",
        "warn-marker-001",
        "warn-marker-002",
    ]


def _verify_conflict_and_overwrite(root: Path, target) -> str:
    case = root / "overwrite"
    source = case / "source.log"
    output = case / "report.json"
    _write(source, _entry("2026-10-24 10:00:00.000", "ERROR", "original-report-marker"))
    _run_cli(target, source, output, "-f", "json")
    original_bytes = output.read_bytes()
    _write(source, _entry("2026-10-24 10:00:00.000", "ERROR", "replacement-report-marker"))
    _assert_conflict_preserves_original(target, source, output, original_bytes)
    _assert_explicit_overwrite(target, source, output, original_bytes)
    return "Export conflict preserves existing bytes; explicit overwrite replaces report"


def _assert_conflict_preserves_original(target, source: Path, output: Path, original_bytes: bytes) -> None:
    conflict = _run_cli(target, source, output, "-f", "json", expect_success=False)
    conflict_text = conflict.stdout + conflict.stderr
    assert conflict.returncode != 0
    assert "--overwrite" in conflict_text
    assert output.name in conflict_text
    assert output.read_bytes() == original_bytes


def _assert_explicit_overwrite(target, source: Path, output: Path, original_bytes: bytes) -> None:
    _run_cli(target, source, output, "-f", "json", "--overwrite")
    replacement = _json(output)
    assert output.read_bytes() != original_bytes
    assert replacement["summary"] == {"ERROR": 1}
    assert [entry["message"] for entry in replacement["results"]] == [
        "replacement-report-marker"
    ]


def _verify_invalid_inputs(root: Path, target) -> str:
    case = root / "invalid-inputs"
    source = case / "logs" / "valid.log"
    _write(source, _entry("2026-10-24 10:00:00.000", "ERROR", "valid-input-marker"))
    broken_xml = case / "broken.xml"
    _write(broken_xml, "<configuration><property name='FILE_LOG_PATTERN' value='%d'/>")
    _assert_invalid_xml(target, source.parent, broken_xml, case / "invalid-xml.json")
    _assert_invalid_pattern(target, source, case / "invalid-pattern.json")
    return "Invalid XML and unsupported pattern fail cleanly without creating reports"


def _assert_invalid_xml(target, source: Path, broken_xml: Path, report: Path) -> None:
    xml_result = _run_cli(
        target,
        source,
        report,
        "-f",
        "json",
        "--logback-xml",
        _runtime_path(target, broken_xml),
        expect_success=False,
    )
    assert xml_result.returncode != 0
    assert broken_xml.name in xml_result.stdout + xml_result.stderr
    assert not report.exists()


def _assert_invalid_pattern(target, source: Path, report: Path) -> None:
    pattern_result = _run_cli(
        target,
        source,
        report,
        "-f",
        "json",
        "--pattern",
        "%d %springProperty%n",
        expect_success=False,
    )
    assert pattern_result.returncode != 0
    assert "%springProperty" in pattern_result.stdout + pattern_result.stderr
    assert not report.exists()


def _verify_encoding_diagnostic(root: Path, target) -> str:
    case = root / "encoding-diagnostic"
    source = case / "invalid-utf8.log"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(
        b"2026-10-24 10:00:00.000 [main] ERROR com.test.App - encoding marker \xff\n"
    )
    output = case / "report.json"
    completed = _run_cli(target, source, output, "-f", "json")
    data = _json(output)
    assert data["summary"] == {"ERROR": 1}
    assert data["results"][0]["message"] == "encoding marker \ufffd"
    assert "UTF-8" in completed.stdout
    assert "\ufffd" in completed.stdout
    return "Invalid UTF-8 replacement is reported while matching record is retained"


def _verify_small_cases(root: Path, target) -> list[str]:
    return [
        _verify_xml_selection(root, target),
        _verify_xml_manual_override(root, target),
        _verify_filters_and_sorting(root, target),
        _verify_multiline_occurrences(root, target),
        _verify_plain_duplicate_occurrences(root, target),
        _verify_level_split_formats(root, target),
    ]


def _verify_bulk_cases(root: Path, target) -> list[str]:
    bulk_source = _bulk_fixture(root)
    return [
        _verify_csv_size_split(root, target, bulk_source),
        _verify_markdown_size_split(root, target, bulk_source),
        _verify_combined_json_split(root, target, bulk_source),
    ]


def _verify_failure_cases(root: Path, target) -> list[str]:
    return [
        _verify_conflict_and_overwrite(root, target),
        _verify_invalid_inputs(root, target),
        _verify_encoding_diagnostic(root, target),
    ]


def verify_advanced_cli(target) -> list[str]:
    """Verify advanced CLI options using only the actual extracted executable."""
    fixture_root = target.data / "advanced-cli"
    fixture_root.mkdir(exist_ok=True)
    root = fixture_root / uuid.uuid4().hex
    root.mkdir()
    return [*_verify_small_cases(root, target), *_verify_bulk_cases(root, target), *_verify_failure_cases(root, target)]
