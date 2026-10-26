from __future__ import annotations

import asyncio
from collections.abc import Iterable

from log_analyzer.domain.log_types import LogEntry


async def find_group_indices(
    groups: Iterable[LogEntry],
    query: str,
    sort_by: str,
    *,
    level: str | None = None,
    group_index: int | None = None,
) -> list[int]:
    """建立符合文字、Level 與索引條件的瀏覽索引，不修改分析明細。

    每掃描一批群組便讓出執行權，讓呼叫端能取消搜尋。未提供 Level 或
    群組索引時，維持既有的文字查詢與排序行為。

    Args:
        groups: 依原始瀏覽順序提供的分析群組。
        query: 搜尋 message、message_body、logger、thread 與逐次 Thread 的文字。
        sort_by: 原始順序、count 或 recent。
        level: 可選的 Level 篩選值；以 strip 與大寫正規化後精確比對。
        group_index: 可選的非負原始索引，用來限定單一群組。

    Returns:
        list[int]: 符合條件並依 sort_by 排序的原始群組索引。

    Raises:
        ValueError: group_index 不是非負整數，或 sort_by 不受支援。
        asyncio.CancelledError: 搜尋在讓出執行權時收到取消要求。
    """
    if group_index is not None and (
        not isinstance(group_index, int)
        or isinstance(group_index, bool)
        or group_index < 0
    ):
        raise ValueError("群組索引必須是非負整數")

    needle = query.strip().casefold()
    normalized_level = level.strip().upper() if level is not None else None
    matches: list[tuple[int, int, str]] = []
    for index, group in enumerate(groups):
        if index % 32 == 0:
            await asyncio.sleep(0)
        if group_index is not None and index < group_index:
            continue
        if group_index is not None and index > group_index:
            break
        level_matches = (
            normalized_level is None
            or bool(normalized_level)
            and str(group.get("level", "") or "").strip().upper() == normalized_level
        )
        if level_matches and (not needle or await _matches(group, needle)):
            matches.append((
                index,
                int(group.get("count", 1)),
                await _latest_timestamp(group) if sort_by == "recent" else "",
            ))
        if group_index is not None:
            break
    if sort_by == "count":
        matches.sort(key=lambda item: (-item[1], item[0]))
    elif sort_by == "recent":
        # Python 穩定排序讓相同時間保留原索引順序。
        matches.sort(key=lambda item: item[2], reverse=True)
    elif sort_by != "original":
        raise ValueError(f"不支援的瀏覽排序：{sort_by}")
    return [item[0] for item in matches]


async def _matches(group: LogEntry, needle: str) -> bool:
    fields = ("message", "message_body", "logger", "thread")
    if any(needle in str(group.get(field) or "").casefold() for field in fields):
        return True
    for index, occurrence in enumerate(group.get("occurrences", ())):
        if index % 512 == 0:
            await asyncio.sleep(0)
        if needle in str(occurrence.get("thread") or "").casefold():
            return True
    return False


async def _latest_timestamp(group: LogEntry) -> str:
    latest = ""
    for index, occurrence in enumerate(group.get("occurrences", ())):
        if index % 512 == 0:
            await asyncio.sleep(0)
        latest = max(latest, str(occurrence.get("timestamp") or ""))
    return latest or str(group.get("last_timestamp") or group.get("timestamp") or "")
