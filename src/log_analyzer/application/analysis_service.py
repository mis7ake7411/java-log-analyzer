from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from ..domain.log_types import MatchedLogs
from ..domain.parser import ParseOptions, parse_log_directory
from ..infrastructure.exporter import export_results
from ..infrastructure.paths import ensure_readable_directory, ensure_writable_directory

DEFAULT_SELECTED_LEVELS = ("ERROR", "WARN", "INFO")


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    """分析執行所需的設定。"""

    path: str
    output_path: str
    start_dt: datetime | None
    end_dt: datetime | None
    keyword: str | None
    ignore_case: bool
    sort_by: str
    fmt: str
    log_pattern: str | None = None
    max_export_bytes: int | None = None
    include_details: bool = True
    levels: tuple[str, ...] | None = DEFAULT_SELECTED_LEVELS
    split_by_level: bool = False


@dataclass(slots=True)
class AnalysisResult:
    """一次分析執行的完整結果"""

    input_path: str
    output_path: str
    format_name: str
    keyword: str
    ignore_case: bool
    total_logs: int
    matched_groups: int
    matched_occurrences: int
    level_summary: list[tuple[str, int]]
    selected_levels: tuple[str, ...]
    sort_by: str
    counts: Counter[str]
    matched_logs: MatchedLogs
    exported_files: list[str]


def build_output_path(output_dir: str, output_name: str, fmt: str) -> str:
    """把輸出資料夾與檔名組合成完整輸出路徑"""
    normalized_dir = ensure_writable_directory(output_dir)
    cleaned = output_name.strip()
    if not cleaned:
        cleaned = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cleaned = os.path.basename(cleaned)
    base_name, _ = os.path.splitext(cleaned)
    if not base_name:
        base_name = cleaned
    return os.path.join(normalized_dir, f"{base_name}.{fmt}")


def run_analysis(  # noqa: PLR0913 - public legacy API retained for backward compatibility
    path: str,
    output_path: str,
    start_dt: datetime | None,
    end_dt: datetime | None,
    keyword: str | None,
    ignore_case: bool,
    sort_by: str,
    fmt: str,
    log_pattern: str | None = None,
    max_export_bytes: int | None = None,
    include_details: bool = True,
    levels: tuple[str, ...] | None = DEFAULT_SELECTED_LEVELS,
    split_by_level: bool = False,
) -> AnalysisResult:
    """公開相容性包裝器，保留既有呼叫介面。"""
    return run_analysis_with_options(
        AnalysisOptions(
            path=path,
            output_path=output_path,
            start_dt=start_dt,
            end_dt=end_dt,
            keyword=keyword,
            ignore_case=ignore_case,
            sort_by=sort_by,
            fmt=fmt,
            log_pattern=log_pattern,
            max_export_bytes=max_export_bytes,
            include_details=include_details,
            levels=levels,
            split_by_level=split_by_level,
        )
    )


def run_analysis_with_options(options: AnalysisOptions) -> AnalysisResult:
    """依分析設定執行分析、匯出，並回傳摘要結果。"""
    normalized_path = ensure_readable_directory(options.path)
    counts, matched_logs = parse_log_directory(
        normalized_path,
        ParseOptions(
            start_time=options.start_dt,
            end_time=options.end_dt,
            keyword=options.keyword,
            ignore_case=options.ignore_case,
            log_pattern=options.log_pattern,
            sort_by=options.sort_by,
            levels=options.levels,
        ),
    )

    if not counts and not matched_logs:
        raise ValueError("找不到符合條件的 log請確認目錄、關鍵字或資料內容")

    exported_files = [
        os.path.abspath(path)
        for path in export_results(
            counts,
            matched_logs,
            options.output_path,
            options.fmt,
            max_export_bytes=options.max_export_bytes,
            split_by_level=options.split_by_level,
        )
    ]

    total_logs = sum(counts.values())
    matched_groups = len(matched_logs)
    matched_occurrences = sum(entry.get("count", 1) for entry in matched_logs)
    level_summary = [(level, count) for level, count in sorted(counts.items()) if count > 0]
    detail_logs: MatchedLogs = matched_logs if options.include_details else []
    if not options.include_details and hasattr(matched_logs, "release"):
        matched_logs.release()
    del matched_logs

    return AnalysisResult(
        input_path=os.path.abspath(normalized_path),
        output_path=os.path.abspath(exported_files[0]),
        format_name=options.fmt,
        keyword=options.keyword or "未設定",
        ignore_case=options.ignore_case,
        total_logs=total_logs,
        matched_groups=matched_groups,
        matched_occurrences=matched_occurrences,
        level_summary=level_summary,
        selected_levels=tuple(options.levels or ()),
        sort_by=options.sort_by,
        counts=counts,
        matched_logs=detail_logs,
        exported_files=exported_files,
    )
