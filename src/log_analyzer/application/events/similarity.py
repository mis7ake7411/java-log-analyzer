"""以訊息文字查找相似事件群組。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite

from rapidfuzz import fuzz

from log_analyzer.domain.log_types import LogEntry

_YIELD_EVERY = 128


@dataclass(frozen=True, slots=True)
class SimilarGroup:
    """相似候選在原分析結果中的索引與文字分數。"""

    group_index: int
    score: float


def normalize_message(message: str) -> str:
    """收斂連續空白並保留訊息大小寫。

    Args:
        message: 原始訊息文字。

    Returns:
        以單一空格分隔的訊息文字；前後空白會移除。

    Raises:
        TypeError: message 不是字串。
    """
    if not isinstance(message, str):
        raise TypeError("message must be a string")
    return " ".join(message.split())


def message_text(group: Mapping[str, object]) -> str:
    """組合事件首行與續行本文，排除堆疊追蹤等欄位。

    Args:
        group: 分析結果中的事件群組。

    Returns:
        只包含 message 與 message_body 的正規化文字。
    """
    parts = [
        value
        for key in ("message", "message_body")
        if isinstance((value := group.get(key)), str) and value
    ]
    return normalize_message(" ".join(parts))


def score_message(reference: str, message: str) -> float:
    """以 case-sensitive fuzz.ratio 計算正規化訊息的相似分數。

    Args:
        reference: 參考訊息。
        message: 待比較訊息。

    Returns:
        介於 0 到 100 的文字相似分數。

    Raises:
        TypeError: 任一輸入不是字串。
    """
    return float(fuzz.ratio(normalize_message(reference), normalize_message(message)))


async def find_similar_groups(
    groups: Iterable[LogEntry],
    reference: str,
    threshold: float = 80,
    exclude_index: int | None = None,
) -> list[SimilarGroup]:
    """依相似門檻搜尋事件群組，並保留穩定的原始索引。

    分數由高至低排列，同分時依群組在來源中的順序排列。掃描會每處理
    一批候選讓出執行權，讓呼叫端的取消要求能及時生效。

    Args:
        groups: 依分析結果原始順序提供的群組。
        reference: 用於比較的參考訊息；空白正規化後不可為空。
        threshold: 最低相似分數，範圍為 0 到 100，預設為 80。
        exclude_index: 要略過的參考群組原始索引。

    Returns:
        依分數遞減、同分時依來源順序排列的相似候選。

    Raises:
        ValueError: 門檻不是 0 到 100 的有限數值，或參考訊息為空。
        TypeError: 門檻或參考訊息的型別不正確。
        asyncio.CancelledError: 掃描期間收到合作式取消要求。
    """
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not isfinite(threshold)
        or not 0 <= threshold <= 100
    ):
        raise ValueError("threshold must be a finite number between 0 and 100")

    normalized_reference = normalize_message(reference)
    if not normalized_reference:
        raise ValueError("reference message cannot be empty")

    matches: list[SimilarGroup] = []
    for group_index, group in enumerate(groups):
        if group_index != exclude_index:
            score = score_message(normalized_reference, message_text(group))
            if score >= threshold:
                matches.append(SimilarGroup(group_index=group_index, score=score))

        if (group_index + 1) % _YIELD_EVERY == 0:
            await asyncio.sleep(0)

    matches.sort(key=lambda item: (-item.score, item.group_index))
    return matches
