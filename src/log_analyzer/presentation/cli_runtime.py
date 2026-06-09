from __future__ import annotations

import os
from typing import Optional

from .datetime_text import parse_datetime_value
from ..domain.logback_xml import find_best_logback_pattern
from ..infrastructure.naming import build_timestamped_name


def resolve_target_dir(target_dir: str):
    """套用預設 logs 資料夾偵測。"""
    normalized_dir = target_dir
    notice = None
    if normalized_dir == "." and os.path.isdir("logs"):
        normalized_dir = "logs"
        notice = "偵測到 'logs' 資料夾，將自動分析該目錄..."
    return normalized_dir, notice


def resolve_output_path(output: Optional[str], fmt: str):
    """產生預設輸出檔名。"""
    if output:
        return output
    return f"{build_timestamped_name('log_analysis')}.{fmt}"


def resolve_logback_pattern(
    logback_xml: Optional[str],
    target_dir: str,
    selected_pattern: Optional[str],
):
    """必要時從 logback.xml 選出最合適的 pattern。"""
    if not logback_xml or selected_pattern:
        return selected_pattern, None

    best_pattern = find_best_logback_pattern(logback_xml, target_dir)
    if best_pattern is None:
        raise ValueError("logback XML 中找不到可用的 pattern。")

    message = (
        "已從 logback XML 選用 pattern："
        f"{best_pattern.name}，命中 {best_pattern.matches}/{best_pattern.checked}"
    )
    return best_pattern.pattern, message
