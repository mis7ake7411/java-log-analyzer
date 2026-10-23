from datetime import datetime, timezone

from log_analyzer.domain.timezone import to_local_timezone


def test_to_local_timezone_assigns_local_timezone_to_naive_value():
    result = to_local_timezone(datetime(2026, 6, 7, 18, 20))

    assert result.replace(tzinfo=None) == datetime(2026, 6, 7, 18, 20)
    assert result.tzinfo == datetime.now().astimezone().tzinfo


def test_to_local_timezone_converts_aware_value_to_local_timezone():
    value = datetime(2026, 6, 7, 10, 20, tzinfo=timezone.utc)

    result = to_local_timezone(value)

    assert result.tzinfo == datetime.now().astimezone().tzinfo
    assert result.astimezone(timezone.utc) == value
