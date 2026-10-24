from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..domain.analysis_control import AnalysisControl
from log_analyzer.domain.log_types import LogEntry, OccurrenceCoordinate


@dataclass(frozen=True, slots=True)
class DashboardAnalysis:
    """Dashboard 所需的彙總資料，不保留原始日誌。"""

    exception_summary: tuple[tuple[str, int, int], ...]
    time_hotspots: tuple[tuple[str, int, int], ...]
    logger_summary: tuple[tuple[str, int, int], ...]
    thread_summary: tuple[tuple[str, int, int], ...]


def aggregate_dashboard_summary(
    matched_logs: Iterable[LogEntry],
    *,
    control: AnalysisControl | None = None,
) -> tuple[DashboardAnalysis, int]:
    """單次走訪命中日誌，計算 dashboard 統計與命中筆數。"""
    exception_occurrences: Counter[str] = Counter()
    exception_groups: Counter[str] = Counter()
    time_occurrences: Counter[str] = Counter()
    time_groups: Counter[str] = Counter()
    logger_occurrences: Counter[str] = Counter()
    logger_groups: Counter[str] = Counter()
    thread_occurrences: Counter[str] = Counter()
    thread_groups: Counter[str] = Counter()
    matched_occurrences = 0
    if control is not None:
        control.report_progress("summarizing", force=True)

    for log in matched_logs:
        if control is not None:
            control.raise_if_cancelled()
        occurrence_count = int(log.get("count", 1))
        matched_occurrences += occurrence_count
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
        ),
        matched_occurrences,
    )


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
