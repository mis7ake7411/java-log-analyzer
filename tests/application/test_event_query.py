import asyncio
from copy import deepcopy

import pytest

from log_analyzer.application.event_query import EventQueryFilters, find_group_indices


def _groups():
    return [
        {"message": "Timeout", "logger": "Service", "thread": "main", "count": 2,
         "last_timestamp": "2026-10-24 10:00:00", "occurrences": [{"thread": "worker-9"}]},
        {"message": "資料錯誤", "logger": "Database", "thread": "pool", "count": 5,
         "last_timestamp": "2026-10-24 11:00:00"},
        {"message": "TIMEOUT", "logger": "Other", "thread": "main", "count": 5,
         "last_timestamp": "2026-10-24 12:00:00"},
    ]


def _find_with_filters(groups, query, sort_by="original", **filter_values):
    return asyncio.run(
        find_group_indices(
            groups,
            query,
            sort_by,
            EventQueryFilters(**filter_values),
        )
    )


def test_query_matches_message_logger_and_every_occurrence_thread_without_mutation():
    """情境：搜尋應涵蓋訊息、Logger 與所有出現位置的 Thread 且不修改原資料。"""
    groups = _groups()
    before = deepcopy(groups)
    assert asyncio.run(find_group_indices(groups, "timeout", "original")) == [0, 2]
    assert asyncio.run(find_group_indices(groups, "database", "original")) == [1]
    assert asyncio.run(find_group_indices(groups, "資料", "original")) == [1]
    assert asyncio.run(find_group_indices(groups, "worker-9", "original")) == [0]
    assert asyncio.run(find_group_indices(groups, "missing", "original")) == []
    assert groups == before


def test_query_sort_is_deterministic_and_keeps_original_indices():
    """情境：排序應可重現並保留對應原始群組索引。"""
    groups = _groups()
    assert asyncio.run(find_group_indices(groups, "", "original")) == [0, 1, 2]
    assert asyncio.run(find_group_indices(groups, "", "count")) == [1, 2, 0]
    assert asyncio.run(find_group_indices(groups, "", "recent")) == [2, 1, 0]


def test_recent_sort_uses_latest_occurrence_even_if_files_were_not_chronological():
    """情境：檔案掃描順序不按時間時最近排序仍應使用最後出現時間。"""
    groups = [
        {"last_timestamp": "2026-10-24 09:00:00", "occurrences": [
            {"timestamp": "2026-10-24 12:00:00"}, {"timestamp": "2026-10-24 09:00:00"},
        ]},
        {"last_timestamp": "2026-10-24 11:00:00", "occurrences": [
            {"timestamp": "2026-10-24 11:00:00"},
        ]},
    ]
    assert asyncio.run(find_group_indices(groups, "", "recent")) == [0, 1]


def test_query_cooperatively_cancels_before_scanning_every_group():
    """情境：搜尋應在掃描全部群組前回應合作式取消。"""
    consumed = []

    def groups():
        for index in range(1000):
            consumed.append(index)
            yield {"message": "sample"}

    async def check():
        task = asyncio.create_task(find_group_indices(groups(), "sample", "original"))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("query did not observe cancellation")
        assert len(consumed) < 1000

    asyncio.run(check())


def test_query_scope_matches_only_selected_retained_field_and_stacktrace_lines():
    groups = [
        {
            "message": "header text",
            "message_body": "Body-only 資料",
            "logger": "com.example.Logger",
            "thread": "group-thread",
            "stacktrace": "retained stack marker",
            "stacktrace_lines": ["ignored line marker"],
            "occurrences": [{"thread": "occurrence-thread"}],
        },
        {
            "message": "Fallback Straße [x]",
            "message_body": "",
            "logger": "other",
            "thread": "pool",
            "stacktrace_lines": ["java.lang.RuntimeException", "  at app.Main.run"],
        },
    ]
    before = deepcopy(groups)

    assert _find_with_filters(groups, "body-only", query_scope="message") == [0]
    assert asyncio.run(
        find_group_indices(
            groups, "header text", "original", EventQueryFilters(query_scope="message")
        )
    ) == [0]
    assert _find_with_filters(groups, "fallback", query_scope="message") == [1]
    assert asyncio.run(
        find_group_indices(
            groups,
            "runtimeexception",
            "original",
            EventQueryFilters(query_scope="stacktrace"),
        )
    ) == [1]
    assert asyncio.run(
        find_group_indices(
            groups,
            "ignored line marker",
            "original",
            EventQueryFilters(query_scope="stacktrace"),
        )
    ) == []
    assert asyncio.run(
        find_group_indices(
            groups,
            "strasse [x]",
            "original",
            EventQueryFilters(query_scope="message"),
        )
    ) == [1]
    assert asyncio.run(
        find_group_indices(
            groups, "資料", "original", EventQueryFilters(query_scope="message")
        )
    ) == [0]
    assert groups == before


def test_independent_filters_and_thread_source_scope_use_literal_and_same_occurrence():
    groups = [
        {
            "logger": "Straße.Logger",
            "thread": "main",
            "filename": "fallback.log",
            "occurrences": [
                {"filename": "target[1].log", "thread": "unrelated"},
                {"filename": "other.log", "thread": "worker-資料"},
            ],
        },
        {
            "logger": "strasse.logger",
            "thread": "main",
            "filename": "other.log",
            "occurrences": [
                {"filename": "target[1].log", "thread": "worker-資料"},
            ],
        },
        {
            "logger": "strasse.logger",
            "thread": "worker-資料",
            "filename": "target[1].log",
            "occurrences": [],
        },
    ]
    before = deepcopy(groups)

    assert _find_with_filters(groups, "", logger_filter="STRASSE") == [0, 1, 2]
    assert _find_with_filters(groups, "", logger_filter="[1].") == []
    assert _find_with_filters(
        groups, "", source_filename_filter="target[1]."
    ) == [0, 1, 2]
    assert _find_with_filters(
        groups,
        "",
        thread_filter="worker-資料",
        source_filename_filter="target[1].",
    ) == [1]
    assert _find_with_filters(
        groups,
        "worker-資料",
        query_scope="thread",
        source_filename_filter="target[1].",
    ) == [1]
    assert _find_with_filters(
        groups,
        "資料",
        query_scope="thread",
        thread_filter="worker",
        source_filename_filter="target[1].",
    ) == [1]
    assert _find_with_filters(groups, "", logger_filter="  ", thread_filter="") == [
        0,
        1,
        2,
    ]
    assert groups == before


def test_advanced_query_keeps_level_and_group_constraints_and_validates_scope_early():
    groups = [
        {"level": "ERROR", "logger": "Service", "count": 1},
        {"level": "WARN", "logger": "Service", "count": 9},
        {"level": "ERROR", "logger": "Service", "count": 4},
    ]

    assert asyncio.run(
        find_group_indices(
            groups,
            "",
            "count",
            EventQueryFilters(level=" error ", query_scope="logger", logger_filter="serv"),
        )
    ) == [2, 0]
    assert asyncio.run(
        find_group_indices(
            groups,
            "",
            "original",
            EventQueryFilters(level="WARN", group_index=1, logger_filter="service"),
        )
    ) == [1]
    with pytest.raises(ValueError, match="搜尋欄位"):
        asyncio.run(
            find_group_indices(
                [], "", "original", EventQueryFilters(query_scope="unknown")
            )
        )
