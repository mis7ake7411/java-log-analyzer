from __future__ import annotations

import os

from .datetime_text import parse_datetime_value
from ..application.analysis_service import DEFAULT_SELECTED_LEVELS
from ..domain.logback_xml import find_best_logback_pattern
from ..infrastructure.naming import build_timestamped_name

__all__ = ["parse_datetime_value"]

ALL_LEVELS = ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")


def resolve_target_dir(target_dir: str):
    """套用預設 logs 資料夾偵測"""
    normalized_dir = target_dir
    notice = None
    if normalized_dir == "." and os.path.isdir("logs"):
        normalized_dir = "logs"
        notice = "偵測到 'logs' 資料夾，將自動分析該目錄..."
    return normalized_dir, notice


def resolve_output_path(output: str | None, fmt: str):
    """產生預設輸出檔名"""
    if output:
        return output
    return f"{build_timestamped_name('log_analysis')}.{fmt}"


def resolve_logback_pattern(
    logback_xml: str | None,
    target_dir: str,
    selected_pattern: str | None,
):
    """必要時從 logback.xml 選出最合適的 pattern"""
    if not logback_xml or selected_pattern:
        return selected_pattern, None

    best_pattern = find_best_logback_pattern(logback_xml, target_dir)
    if best_pattern is None:
        raise ValueError("logback XML 中找不到可用的 pattern")

    message = (
        "已從 logback XML 選用 pattern："
        f"{best_pattern.name}，命中 {best_pattern.matches}/{best_pattern.checked}"
    )
    return best_pattern.pattern, message


def resolve_levels(level_args: list[str] | None) -> tuple[str, ...]:
    """解析 CLI level 篩選，未指定時使用常用預設層級"""
    if not level_args:
        return DEFAULT_SELECTED_LEVELS

    normalized_levels = [raw_level.strip().upper() for raw_level in level_args]
    if "ALL" in normalized_levels:
        return ALL_LEVELS

    levels: list[str] = []
    seen = set()
    for level in normalized_levels:
        if not level or level in seen:
            continue
        levels.append(level)
        seen.add(level)
    return tuple(levels)
