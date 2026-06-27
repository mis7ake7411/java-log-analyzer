from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..domain.parser import parse_logs
from ..infrastructure.exporter import export_results
from ..infrastructure.paths import ensure_readable_directory, ensure_writable_directory


@dataclass(slots=True)
class AnalysisResult:
    """一次分析執行的完整結果。"""

    input_path: str
    output_path: str
    format_name: str
    keyword: str
    ignore_case: bool
    total_logs: int
    matched_groups: int
    matched_occurrences: int
    level_summary: list[tuple[str, int]]
    sort_by: str
    counts: Counter[str]
    matched_logs: Any
    exported_files: list[str]


def build_output_path(output_dir: str, output_name: str, fmt: str) -> str:
    """把輸出資料夾與檔名組合成完整輸出路徑。"""
    normalized_dir = ensure_writable_directory(output_dir)
    cleaned = output_name.strip()
    if not cleaned:
        cleaned = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cleaned = os.path.basename(cleaned)
    base_name, _ = os.path.splitext(cleaned)
    if not base_name:
        base_name = cleaned
    return os.path.join(normalized_dir, f"{base_name}.{fmt}")


def run_analysis(
    path: str,
    output_path: str,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    keyword: Optional[str],
    ignore_case: bool,
    sort_by: str,
    fmt: str,
    log_pattern: Optional[str] = None,
    max_export_bytes: Optional[int] = None,
    include_details: bool = True,
) -> AnalysisResult:
    """執行分析、匯出，並回傳摘要結果。"""
    normalized_path = ensure_readable_directory(path)
    counts, matched_logs = parse_logs(
        normalized_path,
        start_dt,
        end_dt,
        keyword,
        ignore_case=ignore_case,
        log_pattern=log_pattern,
        sort_by=sort_by,
    )

    if not counts and not matched_logs:
        raise ValueError("找不到符合條件的 log。請確認目錄、關鍵字或資料內容。")

    exported_files = [os.path.abspath(path) for path in export_results(counts, matched_logs, output_path, fmt, max_export_bytes)]

    total_logs = sum(counts.values())
    matched_groups = len(matched_logs)
    matched_occurrences = sum(entry.get("count", 1) for entry in matched_logs)
    level_summary = [(level, count) for level, count in sorted(counts.items()) if count > 0]
    detail_logs = matched_logs if include_details else []
    if not include_details and hasattr(matched_logs, "release"):
        matched_logs.release()
    del matched_logs

    return AnalysisResult(
        input_path=os.path.abspath(normalized_path),
        output_path=os.path.abspath(exported_files[0]),
        format_name=fmt,
        keyword=keyword or "未設定",
        ignore_case=ignore_case,
        total_logs=total_logs,
        matched_groups=matched_groups,
        matched_occurrences=matched_occurrences,
        level_summary=level_summary,
        sort_by=sort_by,
        counts=counts,
        matched_logs=detail_logs,
        exported_files=exported_files,
    )
