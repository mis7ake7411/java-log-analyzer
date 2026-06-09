from __future__ import annotations

from collections import Counter

LEVEL_SORT_ORDER = {
    "ERROR": 0,
    "WARN": 1,
    "WARNING": 1,
    "INFO": 2,
    "DEBUG": 3,
    "TRACE": 4,
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
        grouped_logs[key]["count"] += 1
        grouped_logs[key]["last_timestamp"] = entry["timestamp"]
        # 記錄重複項出現的行號
        grouped_logs[key]["line_numbers"].append(entry["line_num"])
    else:
        entry["count"] = 1
        entry["last_timestamp"] = entry["timestamp"]
        entry["stacktrace"] = stacktrace_text
        entry["message_body"] = entry.get("message_body", "")
        grouped_logs[key] = entry


def should_include(text, keyword, ignore_case):
    """
    內部輔助函式：判斷文字是否包含關鍵字。
    """
    if not keyword:
        return True

    target_text = text.lower() if ignore_case else text
    return keyword in target_text
