from __future__ import annotations

import os
from collections import Counter
from datetime import datetime
from typing import Optional

from .logback_pattern import DEFAULT_LOGBACK_REGEX, compile_logback_pattern
from .parser_aggregation import commit_entry, normalize_keyword, sort_key


def parse_logs(
    directory,
    start_time=None,
    end_time=None,
    keyword=None,
    ignore_case=False,
    log_pattern=None,
    sort_by="time",
):
    """
    解析指定資料夾中的所有 Logback 日誌檔案。

    回傳:
        tuple: (各等級的統計數量, 所有符合條件的日誌詳情列表)
    """
    entry_pattern = compile_logback_pattern(log_pattern) if log_pattern else DEFAULT_LOGBACK_REGEX
    counts = Counter()

    if not os.path.exists(directory):
        raise FileNotFoundError(f"找不到目錄: {directory}")

    search_keyword = normalize_keyword(keyword, ignore_case)
    grouped_logs = {}

    for filename in _list_log_files(directory):
        filepath = os.path.join(directory, filename)
        for entry in _iter_entries_from_file(filepath, filename, entry_pattern, start_time, end_time):
            commit_entry(grouped_logs, counts, entry, search_keyword, ignore_case)

    final_logs = sorted(grouped_logs.values(), key=lambda entry: sort_key(entry, sort_by))
    return counts, final_logs


def _list_log_files(directory):
    """回傳資料夾內符合副檔名條件的 log 檔案。"""
    return sorted(filename for filename in os.listdir(directory) if filename.endswith(".log"))


def _iter_entries_from_file(
    filepath,
    filename,
    entry_pattern,
    start_time,
    end_time,
):
    """逐行讀取檔案，產生已完成的 log entry。"""
    current_entry = None

    with open(filepath, "r", encoding="utf-8", errors="ignore") as file_handle:
        for line_num, line in enumerate(file_handle, 1):
            match = entry_pattern.match(line)

            if match:
                if current_entry:
                    yield current_entry
                    current_entry = None

                current_entry = _create_entry(match, filename, line_num)
                if current_entry is None:
                    continue

                entry_time = _parse_entry_time(current_entry["timestamp"])
                if entry_time is None:
                    current_entry = None
                    continue

                if start_time and entry_time < start_time:
                    current_entry = None
                    continue
                if end_time and entry_time > end_time:
                    current_entry = None
                    continue
            elif current_entry:
                _append_stacktrace_line(current_entry, line_num, line)

        if current_entry:
            yield current_entry


def _create_entry(match, filename, line_num):
    """把正則比對結果轉成內部 entry 結構。"""
    timestamp_str = match.group("timestamp")
    thread = match.group("thread")
    level = match.group("level")
    logger = match.group("logger")
    message = match.group("message")

    return {
        "timestamp": timestamp_str,
        "thread": thread,
        "level": level,
        "logger": logger,
        "message": message,
        "filename": filename,
        "line_num": line_num,
        "stacktrace_lines": [],
        "full_text": message,
        "line_numbers": [line_num],  # 追蹤所有重複項出現的行號
    }


def _parse_entry_time(timestamp_str):
    """從日誌時間戳取出可比較的 datetime。"""
    try:
        return datetime.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _append_stacktrace_line(entry, line_num, line):
    """把堆疊追蹤行附加到目前 entry。"""
    entry["stacktrace_lines"].append(f"{line_num:5}: {line}")
    entry["full_text"] += line

