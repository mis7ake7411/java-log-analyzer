from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CorrelatedOccurrence:
    """保留事件中的 MDC 關聯及其原始群組／出現位置索引。"""

    group_index: int
    occurrence_index: int
    timestamp: str
    filename: str
    line_number: int
    thread: str
    level: str
    logger: str
    message: str


def _string_value(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    return value if isinstance(value, str) else ""


async def find_correlated_occurrences(
    groups: Iterable[Mapping[str, object]],
    key: str,
    value: str,
) -> list[CorrelatedOccurrence]:
    """從目前保留的事件群組找出 MDC key/value 完全相符的位置。

    Args:
        groups: 本次分析保留的事件群組；不會重新讀取來源日誌。
        key: 要精確比對的 MDC 欄位名稱，區分大小寫。
        value: 要精確比對的 MDC 欄位值，區分大小寫。

    Returns:
        依時間、檔名、行號及原始索引排序的出現位置清單。

    Raises:
        ValueError: key 或 value 為空字串時。
        asyncio.CancelledError: 呼叫端取消掃描時；取消會向上傳遞。
    """
    if not key or not value:
        raise ValueError("MDC key 和 value 不可為空。")

    matches: list[CorrelatedOccurrence] = []
    groups_since_yield = 0
    occurrences_since_yield = 0

    for group_index, group in enumerate(groups):
        groups_since_yield += 1
        occurrences = group.get("occurrences")
        if isinstance(occurrences, list):
            for occurrence_index, occurrence in enumerate(occurrences):
                occurrences_since_yield += 1
                if isinstance(occurrence, Mapping):
                    mdc = occurrence.get("mdc")
                    if isinstance(mdc, dict) and mdc.get(key) == value:
                        filename = occurrence.get("filename")
                        line_number = occurrence.get("line_number")
                        if (
                            isinstance(filename, str)
                            and isinstance(line_number, int)
                            and not isinstance(line_number, bool)
                        ):
                            matches.append(
                                CorrelatedOccurrence(
                                    group_index=group_index,
                                    occurrence_index=occurrence_index,
                                    timestamp=_string_value(occurrence, "timestamp"),
                                    filename=filename,
                                    line_number=line_number,
                                    thread=_string_value(occurrence, "thread"),
                                    level=_string_value(group, "level"),
                                    logger=_string_value(group, "logger"),
                                    message=_summary_message(group),
                                )
                            )

                if occurrences_since_yield >= 512:
                    occurrences_since_yield = 0
                    await asyncio.sleep(0)

        if groups_since_yield >= 32:
            groups_since_yield = 0
            await asyncio.sleep(0)

    matches.sort(
        key=lambda occurrence: (
            _timestamp_sort_value(occurrence.timestamp),
            occurrence.filename,
            occurrence.line_number,
            occurrence.group_index,
            occurrence.occurrence_index,
        )
    )
    return matches


def _summary_message(group: Mapping[str, object]) -> str:
    header = _string_value(group, "message")
    body = _string_value(group, "message_body")
    if header and body and header != body:
        return f"{header}\n{body}"
    return body or header


def _timestamp_sort_value(timestamp: str) -> str:
    """Normalize Logback's comma or period fraction separator for sorting."""
    return timestamp.replace(",", ".")
