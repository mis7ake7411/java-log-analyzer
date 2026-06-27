from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Iterator, Tuple

_DATE_COMPACT_RE = re.compile(r"^\d{8}$")
_DATE_STANDARD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_HM_COMPACT_RE = re.compile(r"^\d{4}$")
_TIME_HMS_COMPACT_RE = re.compile(r"^\d{6}$")
_DATETIME_COMPACT_RE = re.compile(r"^\d{14}$")
_DATETIME_COMPACT_HM_RE = re.compile(r"^\d{12}$")


def normalize_date_text(value: str) -> str:
    """將純數字日期轉成 YYYY-MM-DD"""
    clean = value.strip()
    if _DATE_COMPACT_RE.fullmatch(clean):
        return f"{clean[:4]}-{clean[4:6]}-{clean[6:]}"
    return clean


def normalize_time_text(value: str) -> str:
    """將純數字時間轉成 HH:MM 或 HH:MM:SS"""
    clean = value.strip()
    if _TIME_HM_COMPACT_RE.fullmatch(clean):
        return f"{clean[:2]}:{clean[2:]}"
    if _TIME_HMS_COMPACT_RE.fullmatch(clean):
        return f"{clean[:2]}:{clean[2:4]}:{clean[4:]}"
    return clean


def parse_datetime_value(value: Optional[str], label: str):
    """解析 CLI 單一時間字串，支援常見純數字格式"""
    if not value:
        return None

    clean = value.strip()
    for candidate, fmt in _build_datetime_candidates(clean):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue

    raise ValueError(
        f"{label}格式不正確請使用 YYYY-MM-DD HH:MM:SS、YYYY-MM-DD HH:MM、"
        "YYYY-MM-DD、YYYYMMDDHHMMSS、YYYYMMDDHHMM 或 YYYYMMDD"
    )


def _build_datetime_candidates(value: str) -> Iterator[Tuple[str, str]]:
    """依字串形狀組出可嘗試解析的日期時間候選"""
    if _DATETIME_COMPACT_RE.fullmatch(value):
        yield value, "%Y%m%d%H%M%S"
        return

    if _DATETIME_COMPACT_HM_RE.fullmatch(value):
        yield value, "%Y%m%d%H%M"
        return

    if _DATE_COMPACT_RE.fullmatch(value):
        yield value, "%Y%m%d"
        return

    if _DATE_STANDARD_RE.fullmatch(value):
        yield value, "%Y-%m-%d"
        return

    if " " in value:
        date_text, time_text = value.split(None, 1)
        normalized_date = normalize_date_text(date_text)
        normalized_time = normalize_time_text(time_text)

        if normalized_date and normalized_time:
            if normalized_time.count(":") == 2:
                yield f"{normalized_date} {normalized_time}", "%Y-%m-%d %H:%M:%S"
            else:
                yield f"{normalized_date} {normalized_time}", "%Y-%m-%d %H:%M"
