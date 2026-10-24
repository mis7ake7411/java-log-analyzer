import pytest

from log_analyzer.domain.parser_aggregation import GroupedLog, _append_line_number, normalize_line_numbers


@pytest.mark.parametrize("numbers, expected", [
    ((1, 2), [1, 2, 3]), ([1, 2], [1, 2, 3]), (None, [3]), (1, [1, 3]),
])
def test_line_number_append_preserves_legacy_values(numbers, expected):
    group = GroupedLog(1, numbers, "time", "time", "ERROR", "thread", "logger", "app.log", [], "message", "", "")
    _append_line_number(group, 3)
    assert normalize_line_numbers(group.line_numbers) == expected


def test_serialized_line_numbers_do_not_alias_the_mutable_accumulator():
    group = GroupedLog(1, [1], "time", "time", "ERROR", "thread", "logger", "app.log", [], "message", "", "")
    serialized = group.to_dict()
    _append_line_number(group, 2)
    assert serialized["line_numbers"] == [1]
    assert group.to_dict()["line_numbers"] == [1, 2]
