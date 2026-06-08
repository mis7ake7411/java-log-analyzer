from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..infrastructure.naming import build_timestamped_name


def get_default_start_date_text() -> str:
    """回傳起始日期預設值。"""
    return ""


def get_default_start_time_text() -> str:
    """回傳起始時間預設值。"""
    return ""


def get_default_end_date_text() -> str:
    """回傳結束日期預設值。"""
    return ""


def get_default_end_time_text() -> str:
    """回傳結束時間預設值。"""
    return ""


def get_default_output_name_text() -> str:
    """回傳輸出檔名預設值。"""
    return build_timestamped_name("analysis")


def parse_datetime_range_inputs(
    start_date_text: str,
    start_time_text: str,
    end_date_text: str,
    end_time_text: str,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """將分離的日期與時間欄位組合成起訖時間。"""
    start_date_clean = start_date_text.strip()
    start_time_clean = start_time_text.strip()
    if not start_date_clean:
        if start_time_clean:
            raise ValueError("請先輸入開始日期，或將開始時間留白。")
        start_dt = None
    elif start_time_clean:
        start_dt = datetime.strptime(f"{start_date_clean} {start_time_clean}", "%Y-%m-%d %H:%M")
    else:
        start_dt = datetime.strptime(start_date_clean, "%Y-%m-%d")

    end_date_clean = end_date_text.strip()
    end_time_clean = end_time_text.strip()
    if not end_date_clean:
        if end_time_clean:
            raise ValueError("請先輸入結束日期，或將結束時間留白。")
        return start_dt, None

    if end_time_clean:
        end_dt = datetime.strptime(f"{end_date_clean} {end_time_clean}", "%Y-%m-%d %H:%M")
    else:
        end_dt = datetime.strptime(end_date_clean, "%Y-%m-%d").replace(
            hour=23,
            minute=59,
            second=59,
        )
    if start_dt is not None and start_dt > end_dt:
        raise ValueError("開始時間不能晚於結束時間。")
    return start_dt, end_dt
