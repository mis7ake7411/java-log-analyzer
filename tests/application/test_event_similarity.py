"""相似事件查找 application service 的行為驗證。"""

import asyncio
from copy import deepcopy
import pytest
from rapidfuzz import fuzz

from log_analyzer.application.events.similarity import (
    find_similar_groups,
    message_text,
    normalize_message,
    score_message,
)


def test_normalize_message_collapses_whitespace_and_preserves_case() -> None:
    """情境：只統一空白，保留大小寫差異。"""
    assert normalize_message("  Failed\tto\nconnect  ") == "Failed to connect"
    assert normalize_message("failed to connect") != "Failed to connect"


def test_message_text_joins_message_and_body_without_stacktrace() -> None:
    """情境：比對文字包含訊息首行與續行，不包含堆疊。"""
    group = {
        "message": "Failed to connect",
        "message_body": "database host",
        "stacktrace": "RuntimeError: unrelated frame",
    }
    assert message_text(group) == "Failed to connect database host"


def test_score_message_uses_case_sensitive_ratio_after_whitespace_normalization() -> None:
    """情境：分數採用 fuzz.ratio，空白正規化後仍區分大小寫。"""
    expected = fuzz.ratio("Failed to connect", "Failed to connect")
    assert score_message(" Failed\tto connect ", "Failed\nto connect") == expected
    assert score_message("failed to connect", "Failed to connect") < 100


def test_find_similar_groups_applies_threshold_and_excludes_reference_group() -> None:
    """情境：依分數門檻回傳候選，並排除參考群組索引。"""
    groups = [
        {"message": "Database connection failed"},
        {"message": "Database connection failed"},
        {"message": "Database connection faild"},
        {"message": "User profile loaded"},
    ]

    matches = asyncio.run(
        find_similar_groups(
            groups,
            "Database connection failed",
            threshold=90,
            exclude_index=0,
        )
    )

    assert [item.group_index for item in matches] == [1, 2]
    assert all(item.score >= 90 for item in matches)
    assert matches[0].score == 100


def test_find_similar_groups_keeps_source_order_for_ties_without_mutation() -> None:
    """情境：同分候選依原群組順序穩定排列且不修改輸入。"""
    groups = [
        {"message": "connection failed", "message_body": "database unavailable"},
        {"message": "connection failed", "message_body": "database unavailable"},
        {"message": "database recovered"},
    ]
    before = deepcopy(groups)

    matches = asyncio.run(
        find_similar_groups(
            groups,
            "connection failed database unavailable",
            threshold=100,
        )
    )

    assert [item.group_index for item in matches] == [0, 1]
    assert [item.score for item in matches] == [100, 100]
    assert groups == before


@pytest.mark.parametrize("threshold", [-0.01, 100.01, float("nan"), float("inf"), -float("inf")])
def test_find_similar_groups_rejects_threshold_outside_finite_percentage(threshold: float) -> None:
    """情境：門檻必須是 0 到 100 的有限數值。"""
    with pytest.raises(ValueError):
        asyncio.run(find_similar_groups([], "reference", threshold=threshold))


def test_find_similar_groups_rejects_empty_normalized_reference() -> None:
    """情境：只含空白的參考訊息不可執行查找。"""
    with pytest.raises(ValueError, match="reference"):
        asyncio.run(find_similar_groups([], " \t\r\n "))


def test_find_similar_groups_cooperatively_cancels_before_scanning_every_group() -> None:
    """情境：大量候選掃描須定期讓出執行權並接受取消。"""
    visited: list[int] = []
    task: asyncio.Task | None = None

    def groups():
        for index in range(50_000):
            visited.append(index)
            if index == 0:
                asyncio.get_running_loop().call_soon(task.cancel)
            yield {"message": "reference"}

    async def run_check() -> None:
        nonlocal task
        task = asyncio.create_task(find_similar_groups(groups(), "reference"))
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_check())

    assert 0 < len(visited) < 50_000
