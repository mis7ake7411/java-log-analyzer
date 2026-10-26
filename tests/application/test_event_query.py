import asyncio
from copy import deepcopy

from log_analyzer.application.event_query import find_group_indices


def _groups():
    return [
        {"message": "Timeout", "logger": "Service", "thread": "main", "count": 2,
         "last_timestamp": "2026-10-24 10:00:00", "occurrences": [{"thread": "worker-9"}]},
        {"message": "資料錯誤", "logger": "Database", "thread": "pool", "count": 5,
         "last_timestamp": "2026-10-24 11:00:00"},
        {"message": "TIMEOUT", "logger": "Other", "thread": "main", "count": 5,
         "last_timestamp": "2026-10-24 12:00:00"},
    ]


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
