from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from log_analyzer.domain.log_types import LogEntry

_QUERY_SCOPES = frozenset({"default", "message", "stacktrace", "logger", "thread"})


@dataclass(frozen=True, slots=True)
class EventQueryFilters:
    """事件瀏覽的可選欄位與群組條件。"""

    query_scope: str = "default"
    logger_filter: str = ""
    thread_filter: str = ""
    source_filename_filter: str = ""
    level: str | None = None
    group_index: int | None = None

    @property
    def has_occurrence_filter(self) -> bool:
        return bool(self.thread_filter or self.source_filename_filter)


@dataclass(slots=True)
class _OccurrenceMatches:
    query: bool = False
    filters: bool = False
    query_and_filters: bool = False
    has_occurrences: bool = False


@dataclass(frozen=True, slots=True)
class _OccurrenceSearch:
    needle: str
    filters: EventQueryFilters
    group_query_match: bool
    require_thread_query_with_filters: bool


async def find_group_indices(
    groups: Iterable[LogEntry],
    query: str,
    sort_by: str,
    filters: EventQueryFilters | None = None,
    **filter_values: object,
) -> list[int]:
    """建立符合文字、欄位與群組條件的瀏覽索引，不修改分析明細。

    預設文字查詢維持搜尋 message、message_body、logger、thread 與逐次
    Thread；明確選擇訊息欄位時會同時搜尋首行與續行本文。

    Args:
        groups: 依原始瀏覽順序提供的分析群組。
        query: 要搜尋的字面子字串；忽略大小寫。
        sort_by: 原始順序、count 或 recent。
        filters: 欄位、Logger、Thread、來源檔名、Level 與群組索引條件；
            舊呼叫也可將條件直接傳入關鍵字參數。

    Returns:
        list[int]: 符合條件並依 sort_by 排序的原始群組索引。

    Raises:
        ValueError: 搜尋欄位、排序值或群組索引不受支援。
        asyncio.CancelledError: 搜尋在讓出執行權時收到取消要求。
    """
    filters = _resolve_filters(filters, filter_values)
    if filters.group_index is not None and (
        not isinstance(filters.group_index, int)
        or isinstance(filters.group_index, bool)
        or filters.group_index < 0
    ):
        raise ValueError("群組索引必須是非負整數")
    if filters.query_scope not in _QUERY_SCOPES:
        raise ValueError(f"不支援的搜尋欄位：{filters.query_scope}")
    if sort_by not in {"original", "count", "recent"}:
        raise ValueError(f"不支援的瀏覽排序：{sort_by}")

    needle = query.strip().casefold()
    normalized_filters = EventQueryFilters(
        query_scope=filters.query_scope,
        logger_filter=_normalize_filter(filters.logger_filter),
        thread_filter=_normalize_filter(filters.thread_filter),
        source_filename_filter=_normalize_filter(filters.source_filename_filter),
        level=filters.level.strip().upper() if filters.level is not None else None,
        group_index=filters.group_index,
    )
    matches: list[tuple[int, int, str]] = []
    for index, group in enumerate(groups):
        if index % 32 == 0:
            await asyncio.sleep(0)
        if (
            normalized_filters.group_index is not None
            and index < normalized_filters.group_index
        ):
            continue
        if (
            normalized_filters.group_index is not None
            and index > normalized_filters.group_index
        ):
            break
        level_matches = (
            normalized_filters.level is None
            or bool(normalized_filters.level)
            and str(group.get("level", "") or "").strip().upper()
            == normalized_filters.level
        )
        if level_matches and await _matches(group, needle, normalized_filters):
            matches.append(
                (
                    index,
                    int(group.get("count", 1)),
                    await _latest_timestamp(group) if sort_by == "recent" else "",
                )
            )
        if normalized_filters.group_index is not None:
            break
    if sort_by == "count":
        matches.sort(key=lambda item: (-item[1], item[0]))
    elif sort_by == "recent":
        # Python 穩定排序讓相同時間保留原索引順序。
        matches.sort(key=lambda item: item[2], reverse=True)
    return [item[0] for item in matches]


async def _matches(
    group: LogEntry,
    needle: str,
    filters: EventQueryFilters,
) -> bool:
    if filters.logger_filter and not _contains(group.get("logger"), filters.logger_filter):
        return False

    group_query_match = _group_query_matches(group, needle, filters.query_scope)
    require_thread_query_with_filters = bool(
        filters.query_scope == "thread" and needle and filters.has_occurrence_filter
    )
    if require_thread_query_with_filters:
        group_query_match = False
    search = _OccurrenceSearch(
        needle=needle,
        filters=filters,
        group_query_match=group_query_match,
        require_thread_query_with_filters=require_thread_query_with_filters,
    )

    needs_occurrence_scan = bool(
        filters.has_occurrence_filter
        or needle
        and filters.query_scope in {"default", "thread"}
        and not group_query_match
    )
    occurrence_matches = (
        await _scan_occurrences(
            group.get("occurrences", ()),
            search,
        )
        if needs_occurrence_scan
        else _OccurrenceMatches()
    )
    query_matches = _query_matches(search, occurrence_matches)
    if not query_matches:
        return False
    if not filters.has_occurrence_filter:
        return True
    if occurrence_matches.filters:
        return True
    return _matches_source_fallback(group, filters, occurrence_matches)


async def _scan_occurrences(
    occurrences: object,
    search: _OccurrenceSearch,
) -> _OccurrenceMatches:
    if not isinstance(occurrences, Iterable):
        return _OccurrenceMatches()

    matches = _OccurrenceMatches()
    for index, occurrence in enumerate(occurrences):
        if index % 512 == 0:
            await asyncio.sleep(0)
        matches.has_occurrences = True
        if not isinstance(occurrence, Mapping):
            continue

        occurrence_query_match = bool(
            search.needle
            and search.filters.query_scope in {"default", "thread"}
            and _contains(occurrence.get("thread"), search.needle)
        )
        occurrence_filter_match = _occurrence_matches_filters(
            occurrence, search.filters
        )
        matches.query = matches.query or occurrence_query_match
        matches.filters = matches.filters or occurrence_filter_match
        matches.query_and_filters = (
            matches.query_and_filters
            or occurrence_query_match and occurrence_filter_match
        )
        if _occurrence_scan_is_resolved(search, matches):
            break
    return matches


def _occurrence_scan_is_resolved(
    search: _OccurrenceSearch,
    matches: _OccurrenceMatches,
) -> bool:
    if search.require_thread_query_with_filters:
        return matches.query_and_filters
    query_match = not search.needle or search.group_query_match or matches.query
    filters_match = not search.filters.has_occurrence_filter or matches.filters
    return query_match and filters_match


def _query_matches(
    search: _OccurrenceSearch,
    matches: _OccurrenceMatches,
) -> bool:
    if not search.needle:
        return True
    if search.require_thread_query_with_filters:
        return matches.query_and_filters
    return search.group_query_match or (
        search.filters.query_scope in {"default", "thread"} and matches.query
    )


def _resolve_filters(
    filters: EventQueryFilters | None,
    filter_values: dict[str, object],
) -> EventQueryFilters:
    if filters is not None:
        if filter_values:
            raise TypeError("請使用 filters 或直接條件參數其中一種傳法")
        return filters
    return EventQueryFilters(**filter_values)


def _group_query_matches(group: LogEntry, needle: str, query_scope: str) -> bool:
    if not needle:
        return True
    if query_scope == "default":
        return any(
            _contains(group.get(field), needle)
            for field in ("message", "message_body", "logger", "thread")
        )
    if query_scope == "message":
        return _contains(group.get("message"), needle) or _contains(
            group.get("message_body"), needle
        )
    if query_scope == "stacktrace":
        return _contains(_stacktrace_text(group), needle)
    if query_scope == "logger":
        return _contains(group.get("logger"), needle)
    return _contains(group.get("thread"), needle)


def _stacktrace_text(group: LogEntry) -> str:
    stacktrace = group.get("stacktrace")
    if isinstance(stacktrace, str) and stacktrace:
        return stacktrace
    lines = group.get("stacktrace_lines")
    if not isinstance(lines, list):
        return ""
    return "\n".join(line for line in lines if isinstance(line, str))


def _occurrence_matches_filters(
    occurrence: Mapping[str, object], filters: EventQueryFilters
) -> bool:
    return (
        (not filters.thread_filter or _contains(occurrence.get("thread"), filters.thread_filter))
        and (
            not filters.source_filename_filter
            or _contains(occurrence.get("filename"), filters.source_filename_filter)
        )
    )


def _matches_source_fallback(
    group: LogEntry,
    filters: EventQueryFilters,
    occurrence_matches: _OccurrenceMatches,
) -> bool:
    return bool(
        filters.source_filename_filter
        and not filters.thread_filter
        and not occurrence_matches.has_occurrences
        and _contains(group.get("filename"), filters.source_filename_filter)
    )


def _normalize_filter(value: str) -> str:
    return value.strip().casefold()


def _contains(value: object, needle: str) -> bool:
    return needle in str(value or "").casefold()


async def _latest_timestamp(group: LogEntry) -> str:
    latest = ""
    for index, occurrence in enumerate(group.get("occurrences", ())):
        if index % 512 == 0:
            await asyncio.sleep(0)
        latest = max(latest, str(occurrence.get("timestamp") or ""))
    return latest or str(group.get("last_timestamp") or group.get("timestamp") or "")
