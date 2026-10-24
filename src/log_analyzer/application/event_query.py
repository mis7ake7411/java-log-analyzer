from __future__ import annotations

import asyncio
from collections.abc import Iterable

from log_analyzer.domain.log_types import LogEntry


async def find_group_indices(
    groups: Iterable[LogEntry], query: str, sort_by: str
) -> list[int]:
    """只建立瀏覽索引，不複製或修改分析明細；分批讓出執行權以便取消。"""
    needle = query.strip().casefold()
    matches: list[tuple[int, int, str]] = []
    for index, group in enumerate(groups):
        if index % 32 == 0:
            await asyncio.sleep(0)
        if not needle or await _matches(group, needle):
            matches.append((
                index,
                int(group.get("count", 1)),
                await _latest_timestamp(group) if sort_by == "recent" else "",
            ))
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
