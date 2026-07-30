from datetime import datetime

from log_analyzer.presentation.cli_runtime import parse_datetime_value, resolve_levels


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
