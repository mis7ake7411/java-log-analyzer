import asyncio
from copy import deepcopy

import pytest

from log_analyzer.application.analysis.summary import (
    DashboardAnalysis,
    aggregate_dashboard_summary,
)
from log_analyzer.application.events.query import find_group_indices


class _SinglePassLogs:
    def __init__(self, logs):
        self.logs = logs
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("matched logs were traversed more than once")
        yield from self.logs


def _log(*, count=1, level="INFO", message="sample", **fields):
    return {
        "count": count,
        "level": level,
        "message": message,
        **fields,
    }


def test_top_events_keep_five_highest_counts_with_stable_original_indices():
    """情境：鑽取事件只保留前五名，並以原始索引穩定處理同數量。"""
    logs = _SinglePassLogs([
        _log(count=4, message="index zero"),
        _log(count=10, message="index one"),
        _log(count=10, message="index two"),
        _log(count=2, message="index three"),
        _log(count=10, message="index four"),
        _log(count=4, message="index five"),
        _log(count=1, message="index six"),
        _log(count=7, message="index seven"),
    ])

    summary, total = aggregate_dashboard_summary(logs)

    assert [
        (event.group_index, event.level, event.message, event.count)
        for event in summary.top_events
    ] == [
        (1, "INFO", "index one", 10),
        (2, "INFO", "index two", 10),
        (4, "INFO", "index four", 10),
        (7, "INFO", "index seven", 7),
        (0, "INFO", "index zero", 4),
    ]
    assert len(summary.top_events) == 5
    assert total == 48
    assert logs.iterations == 1


def test_top_event_message_uses_normalized_first_line_and_is_bounded():
    """情境：鑽取訊息取代表內容第一行、折疊空白並限制為 160 字元。"""
    body = " \tHeader\t   with spaces " + ("X" * 200) + "\nignored second line"
    summary, _ = aggregate_dashboard_summary([
        _log(message_body=body, message="fallback message"),
    ])

    event = summary.top_events[0]
    expected = ("Header with spaces " + ("X" * 200))[:160]
    assert event.message == expected
    assert len(event.message) == 160
    assert "ignored second line" not in event.message


def test_dashboard_analysis_keeps_four_positional_arguments_compatible():
    """情境：既有四個位置參數的 DashboardAnalysis 呼叫仍可使用。"""
    analysis = DashboardAnalysis((), (), (), ())

    assert analysis.top_events == ()


def test_summary_top_events_do_not_mutate_or_retain_input_groups():
    """情境：產生鑽取資料不得修改輸入群組或逐次出現位置。"""
    groups = [
        _log(
            count=3,
            message_body="representative",
            occurrences=[{"timestamp": "2026-10-26 10:00:00", "thread": "worker-a"}],
        ),
    ]
    before = deepcopy(groups)

    summary, _ = aggregate_dashboard_summary(groups)

    assert groups == before
    event = summary.top_events[0]
    assert event is not groups[0]
    assert (event.group_index, event.message, event.count) == (0, "representative", 3)


def test_level_filter_is_exact_and_never_searches_message_text():
    """情境：Level 篩選只比對正規化後的 level，不搜尋訊息字樣。"""
    groups = [
        _log(level=" ERROR ", message="database timeout"),
        _log(level="INFO", message="ERROR shown in this message"),
        _log(level="ERROR", message="database reset"),
        _log(level="WARN", message="database warning"),
    ]

    assert asyncio.run(find_group_indices(groups, "", "original", level=" error ")) == [0, 2]
    assert asyncio.run(find_group_indices(groups, "", "original", level="")) == []
    assert asyncio.run(find_group_indices(groups, "", "original", level="UNKNOWN")) == []
    assert asyncio.run(find_group_indices(groups, "", "original", level=None)) == [0, 1, 2, 3]


def test_group_index_filter_matches_zero_and_unknown_indices_without_widening():
    """情境：指定索引零只回傳該群組，找不到目標時維持空結果。"""
    groups = [
        _log(level="ERROR", message="database timeout"),
        _log(level="INFO", message="database notice"),
    ]

    assert asyncio.run(
        find_group_indices(groups, "timeout", "original", group_index=0)
    ) == [0]
    assert asyncio.run(
        find_group_indices(groups, "database", "original", level="ERROR", group_index=1)
    ) == []
    assert asyncio.run(find_group_indices(groups, "", "original", group_index=99)) == []


@pytest.mark.parametrize(
    "group_index",
    [-1, 1.5, "0", True],
    ids=["negative", "fractional", "string", "boolean"],
)
def test_group_index_filter_rejects_values_that_are_not_nonnegative_integers(group_index):
    """情境：指定索引必須是非負整數，無效值明確拋出 ValueError。"""
    with pytest.raises(ValueError):
        asyncio.run(find_group_indices([], "", "original", group_index=group_index))


def test_level_query_filters_intersect_with_count_recent_sort_and_group_index():
    """情境：Level、文字與索引條件交集後仍依指定排序回傳原始索引。"""
    groups = [
        _log(
            level="ERROR",
            message="Database timeout",
            count=2,
            occurrences=[{"timestamp": "2026-10-26 13:00:00"}],
        ),
        _log(
            level="ERROR",
            message="Database reset",
            count=11,
            occurrences=[{"timestamp": "2026-10-26 10:00:00"}],
        ),
        _log(
            level="WARN",
            message="Database timeout",
            count=7,
            occurrences=[{"timestamp": "2026-10-26 14:00:00"}],
        ),
        _log(
            level="ERROR",
            message="File error",
            count=99,
            occurrences=[{"timestamp": "2026-10-26 15:00:00"}],
        ),
    ]
    before = deepcopy(groups)

    assert asyncio.run(
        find_group_indices(groups, "database", "count", level="error")
    ) == [1, 0]
    assert asyncio.run(
        find_group_indices(groups, "database", "recent", level=" error ")
    ) == [0, 1]
    assert asyncio.run(
        find_group_indices(
            groups,
            "database",
            "recent",
            level="ERROR",
            group_index=1,
        )
    ) == [1]
    assert asyncio.run(
        find_group_indices(groups, "missing", "count", level="ERROR")
    ) == []
    assert groups == before
