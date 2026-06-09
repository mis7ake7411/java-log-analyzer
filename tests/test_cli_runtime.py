from log_analyzer.presentation.cli_runtime import parse_datetime_value


def test_parse_datetime_value_accepts_compact_numeric_date_only():
    value = parse_datetime_value("20260607", "開始時間")

    assert value.isoformat(sep=" ") == "2026-06-07 00:00:00"


def test_parse_datetime_value_accepts_compact_numeric_datetime():
    value = parse_datetime_value("20260607 182000", "開始時間")

    assert value.isoformat(sep=" ") == "2026-06-07 18:20:00"


def test_parse_datetime_value_accepts_compact_numeric_datetime_without_seconds():
    value = parse_datetime_value("202606071820", "開始時間")

    assert value.isoformat(sep=" ") == "2026-06-07 18:20:00"
