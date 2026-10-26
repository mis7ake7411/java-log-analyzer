from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from heapq import heappush, heapreplace

from ..domain.analysis_control import AnalysisControl
from log_analyzer.domain.log_types import LogEntry, OccurrenceCoordinate

_TOP_EVENT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class DashboardEvent:
    """Dashboard 鑽取摘要，只保存索引與受限的代表文字。"""

    group_index: int
    level: str
    message: str
    count: int


@dataclass(frozen=True, slots=True)
class DashboardAnalysis:
    """Dashboard 所需的彙總資料，不保留原始日誌。"""

    exception_summary: tuple[tuple[str, int, int], ...]
    time_hotspots: tuple[tuple[str, int, int], ...]
    logger_summary: tuple[tuple[str, int, int], ...]
    thread_summary: tuple[tuple[str, int, int], ...]
    top_events: tuple[DashboardEvent, ...] = ()


def aggregate_dashboard_summary(
    matched_logs: Iterable[LogEntry],
    *,
    control: AnalysisControl | None = None,
) -> tuple[DashboardAnalysis, int]:
    """單次走訪命中日誌，計算摘要、前五個鑽取事件與命中筆數。

    Args:
        matched_logs: 依瀏覽順序提供的命中群組，可為單次走訪 iterable。
        control: 可選的進度與取消控制器。

    Returns:
        tuple[DashboardAnalysis, int]: Dashboard 摘要與加權命中次數。

    Raises:
        AnalysisCancelled: control 收到取消要求時停止彙總。
    """
    exception_occurrences: Counter[str] = Counter()
    exception_groups: Counter[str] = Counter()
    time_occurrences: Counter[str] = Counter()
    time_groups: Counter[str] = Counter()
    logger_occurrences: Counter[str] = Counter()
    logger_groups: Counter[str] = Counter()
    thread_occurrences: Counter[str] = Counter()
    thread_groups: Counter[str] = Counter()
    top_events: list[tuple[tuple[int, int], DashboardEvent]] = []
    matched_occurrences = 0
    if control is not None:
        control.report_progress("summarizing", force=True)

    for group_index, log in enumerate(matched_logs):
        if control is not None:
            control.raise_if_cancelled()
        occurrence_count = int(log.get("count", 1))
        matched_occurrences += occurrence_count
        event = DashboardEvent(
            group_index=group_index,
            level=str(log.get("level", "") or "").strip(),
            message=_dashboard_event_message(log),
            count=occurrence_count,
        )
        rank = (event.count, -event.group_index)
        if len(top_events) < _TOP_EVENT_LIMIT:
            heappush(top_events, (rank, event))
        elif rank > top_events[0][0]:
            heapreplace(top_events, (rank, event))
        _add_summary_value(
            _extract_exception_signature(log),
            occurrence_count,
            exception_occurrences,
            exception_groups,
        )
        times, threads = _occurrence_distributions(log, occurrence_count, control)
        for value, count in times.items():
            _add_summary_value(value, count, time_occurrences, time_groups)
        for value, count in threads.items():
            _add_summary_value(value, count, thread_occurrences, thread_groups)
        _add_summary_value(
            str(log.get("logger", "") or "").strip(),
            occurrence_count,
            logger_occurrences,
            logger_groups,
        )

    if control is not None:
        control.raise_if_cancelled()

    return (
        DashboardAnalysis(
            exception_summary=_build_summary(exception_occurrences, exception_groups),
            time_hotspots=_build_summary(time_occurrences, time_groups),
            logger_summary=_build_summary(logger_occurrences, logger_groups),
            thread_summary=_build_summary(thread_occurrences, thread_groups),
            top_events=tuple(
                sorted(
                    (item[1] for item in top_events),
                    key=lambda item: (-item.count, item.group_index),
                )
            ),
        ),
        matched_occurrences,
    )


def _dashboard_event_message(log: LogEntry) -> str:
    """取代表訊息第一行並折疊空白，避免鑽取摘要攜帶長內文。"""
    message = str(log.get("message_body") or log.get("message") or "")
    line_end = len(message)
    for separator in ("\r", "\n"):
        position = message.find(separator)
        if position >= 0:
            line_end = min(line_end, position)
    first_line = message[:line_end]
    return " ".join(first_line.split())[:160]


def _occurrence_distributions(
    log: LogEntry, occurrence_count: int, control: AnalysisControl | None
) -> tuple[Counter[str], Counter[str]]:
    """先在群組內計次，讓跨時段／Thread 的群組在各分類只計一組。"""
    times: Counter[str] = Counter()
    threads: Counter[str] = Counter()
    occurrences = log.get("occurrences")
    if not occurrences:
        # 舊資料未保留逐次座標時，維持代表欄位的加權統計。
        times[_extract_time_bucket(log)] = occurrence_count
        threads[str(log.get("thread", "") or "").strip()] = occurrence_count
        return times, threads
    for occurrence in occurrences:
        if control is not None:
            control.raise_if_cancelled()
        times[_extract_time_bucket(occurrence)] += 1
        threads[str(occurrence.get("thread", "") or "").strip()] += 1
    return times, threads


def _add_summary_value(
    value: str,
    occurrence_count: int,
    occurrences: Counter[str],
    group_counts: Counter[str],
) -> None:
    if not value:
        return
    occurrences[value] += occurrence_count
    group_counts[value] += 1


def _build_summary(
    occurrences: Counter[str],
    group_counts: Counter[str],
) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        sorted(
            (
                (value, group_counts[value], occurrence_count)
                for value, occurrence_count in occurrences.items()
            ),
            key=lambda item: (-item[2], -item[1], item[0]),
        )
    )


def _extract_time_bucket(log: LogEntry | OccurrenceCoordinate) -> str:
    timestamp = str(log.get("timestamp", "") or "").strip()
    if not timestamp:
        return ""

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(timestamp[:26], fmt)
            return parsed.strftime("%Y-%m-%d %H:00")
        except ValueError:
            continue
    return timestamp[:13] + ":00" if len(timestamp) >= 13 else ""


def _extract_exception_signature(log: LogEntry) -> str:
    stacktrace = str(log.get("stacktrace", "") or "").strip()
    if stacktrace:
        first_line = stacktrace.splitlines()[0]
        cleaned = re.sub(r"^\s*\d+:\s*", "", first_line).strip()
        if cleaned:
            return cleaned

    message_body = str(log.get("message_body", "") or "").strip()
    if message_body:
        for line in message_body.splitlines():
            cleaned = line.strip()
            if cleaned and (
                "Exception" in cleaned or "Error" in cleaned or cleaned.startswith("Caused by:")
            ):
                return cleaned

    message = str(log.get("message", "") or "").strip()
    if "Exception" in message or "Error" in message:
        return message.splitlines()[0].strip()

    return ""
