from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

LEVEL_SORT_ORDER = {
    "ERROR": 0,
    "WARN": 1,
    "WARNING": 1,
    "INFO": 2,
    "DEBUG": 3,
    "TRACE": 4,
}

_LOG_FIELDS = {
    "count",
    "line_numbers",
    "timestamp",
    "last_timestamp",
    "level",
    "thread",
    "logger",
    "filename",
    "message",
    "message_body",
    "stacktrace",
}


@dataclass(slots=True)
class GroupedLog:
    count: int
    line_numbers: tuple[int, ...]
    timestamp: str
    last_timestamp: str
    level: str
    thread: str
    logger: str
    filename: str
    message: str
    message_body: str
    stacktrace: str

    def __getitem__(self, key):
        if key == "line_numbers":
            return normalize_line_numbers(self.line_numbers)
        if key in _LOG_FIELDS:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return key in _LOG_FIELDS

    def to_dict(self):
        return {
            "count": self.count,
            "line_numbers": normalize_line_numbers(self.line_numbers),
            "timestamp": self.timestamp,
            "last_timestamp": self.last_timestamp,
            "level": self.level,
            "thread": self.thread,
            "logger": self.logger,
            "filename": self.filename,
            "message": self.message,
            "message_body": self.message_body,
            "stacktrace": self.stacktrace,
        }


def normalize_keyword(keyword, ignore_case):
    """統一關鍵字比對前的大小寫處理。"""
    if keyword and ignore_case:
        return keyword.lower()
    return keyword


def sort_key(entry, sort_by):
    timestamp = entry["timestamp"]
    if sort_by == "level":
        return (
            LEVEL_SORT_ORDER.get(entry["level"], 99),
            timestamp,
        )
    return (timestamp,)


def commit_entry(grouped_logs, counts, entry, keyword, ignore_case):
    """
    內部輔助函式：套用文字過濾後，才將 log 納入統計與分組。
    """
    if not should_include(entry["full_text"], keyword, ignore_case):
        return

    counts[entry["level"]] += 1
    add_to_grouped_logs(grouped_logs, entry)


def add_to_grouped_logs(grouped_logs, entry):
    """
    內部輔助函式：將新的 log 加入分組中。
    """
    message_text = entry["message"]
    if entry.get("message_body"):
        message_text = f"{message_text}\n{entry['message_body']}"

    stacktrace_text = "".join(entry["stacktrace_lines"]).strip()
    key = (entry["level"], entry["logger"], message_text, stacktrace_text)

    if key in grouped_logs:
        grouped_logs[key].count += 1
        grouped_logs[key].last_timestamp = entry["timestamp"]
        # 記錄重複項出現的行號
        _append_line_number(grouped_logs[key], entry["line_num"])
    else:
        grouped_logs[key] = GroupedLog(
            count=1,
            line_numbers=(entry["line_num"],),
            timestamp=entry["timestamp"],
            last_timestamp=entry["timestamp"],
            level=entry["level"],
            thread=entry["thread"],
            logger=entry["logger"],
            filename=entry["filename"],
            message=message_text,
            message_body=entry.get("message_body", ""),
            stacktrace=stacktrace_text,
        )


def _append_line_number(entry, line_num):
    line_numbers = entry.line_numbers
    if isinstance(line_numbers, tuple):
        entry.line_numbers = line_numbers + (line_num,)
        return

    if line_numbers is None:
        entry.line_numbers = (line_num,)
        return

    entry.line_numbers = (line_numbers, line_num)


def normalize_line_numbers(line_numbers):
    if isinstance(line_numbers, list):
        return line_numbers
    if isinstance(line_numbers, tuple):
        return list(line_numbers)
    if line_numbers is None:
        return []
    return [line_numbers]


def should_include(text, keyword, ignore_case):
    """
    內部輔助函式：判斷文字是否包含關鍵字。
    """
    if not keyword:
        return True

    target_text = text.lower() if ignore_case else text
    return keyword in target_text
