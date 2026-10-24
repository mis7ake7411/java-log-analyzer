from datetime import datetime
from types import SimpleNamespace

import pytest

from log_analyzer.presentation.cli import runtime
from log_analyzer.presentation.cli.runtime import parse_datetime_value, resolve_levels


def test_parse_datetime_value_accepts_compact_numeric_date_only():
    value = parse_datetime_value("20260607", "開始時間")

    assert value.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 00:00:00"


def test_parse_datetime_value_accepts_compact_numeric_datetime():
    value = parse_datetime_value("20260607 182000", "開始時間")

    assert value.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 18:20:00"


def test_parse_datetime_value_accepts_compact_numeric_datetime_without_seconds():
    value = parse_datetime_value("202606071820", "開始時間")

    assert value.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 18:20:00"


def test_parse_datetime_value_assigns_system_local_timezone():
    value = parse_datetime_value("202606071820", "開始時間")

    assert value.tzinfo == datetime.now().astimezone().tzinfo


def test_resolve_levels_defaults_to_common_levels():
    assert resolve_levels(None) == ("ERROR", "WARN", "INFO")


def test_resolve_levels_keeps_order_and_removes_duplicates():
    assert resolve_levels(["debug", "ERROR", "debug"]) == ("DEBUG", "ERROR")


def test_resolve_levels_all_expands_to_every_supported_level():
    assert resolve_levels(["ALL"]) == ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")


def test_resolve_levels_all_takes_precedence_when_mixed_with_other_levels():
    assert resolve_levels(["ERROR", "ALL"]) == ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")


def test_resolve_logback_pattern_passes_frozen_files_to_xml_sampling(monkeypatch, tmp_path):
    selected_files = ("chosen.LOG",)
    captured_call = None
    best_pattern = SimpleNamespace(
        name="FILE_LOG_PATTERN",
        pattern="%msg%n",
        matches=1,
        checked=1,
        xml_path="logback.xml",
    )

    def assess(xml_paths, source_path, *, selected_files=None):
        nonlocal captured_call
        captured_call = (tuple(xml_paths), source_path, selected_files)
        return SimpleNamespace(
            status="ready",
            selected=best_pattern,
            reason="",
        )

    monkeypatch.setattr(runtime, "assess_logback_patterns", assess, raising=False)

    pattern, notice = runtime.resolve_logback_pattern(
        "logback.xml",
        str(tmp_path),
        None,
        selected_files=selected_files,
    )

    assert captured_call == (("logback.xml",), str(tmp_path), selected_files)
    assert pattern == "%msg%n"
    assert notice == "已從 logback XML 選用 pattern：FILE_LOG_PATTERN，命中 1/1"


def test_resolve_logback_pattern_rejects_uncertain_xml_candidate(tmp_path):
    xml_path = tmp_path / "logback.xml"
    xml_path.write_text(
        '<configuration><property name="LOG_PATTERN" value="%d [%thread] %-5level %logger - %msg%n"/></configuration>',
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("not a log line\n", encoding="utf-8")

    with pytest.raises(ValueError, match="--pattern"):
        runtime.resolve_logback_pattern(str(xml_path), str(log_dir), None)


def test_resolve_logback_pattern_reports_unsupported_candidate_error(tmp_path):
    xml_path = tmp_path / "logback.xml"
    xml_path.write_text(
        '<configuration><property name="FILE_LOG_PATTERN" value="%d %springProperty%n"/></configuration>',
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("sample\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        runtime.resolve_logback_pattern(str(xml_path), str(log_dir), None)

    assert "FILE_LOG_PATTERN" in str(exc_info.value)
    assert "%springProperty" in str(exc_info.value)
