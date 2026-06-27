from __future__ import annotations

from ..domain.parser_aggregation import normalize_line_numbers


def render_markdown_report(counts, matched_logs) -> str:
    """將分析結果渲染為 Markdown 內容字串。"""
    parts = [render_markdown_prefix(counts)]
    if not matched_logs:
        parts.append("無符合條件的記錄。\n")
    else:
        for i, log in enumerate(matched_logs, 1):
            parts.append(serialize_markdown_log(i, log))
    return "".join(parts)


def render_markdown_prefix(counts) -> str:
    """渲染 Markdown 報表前綴，不包含詳細記錄。"""
    parts = ["# Java Log 分析報告\n\n"]
    parts.append("## 統計摘要\n")
    parts.append("| 日誌等級 | 數量 |\n")
    parts.append("| --- | --- |\n")
    for level, count in sorted(counts.items()):
        parts.append(f"| {level} | {count} |\n")

    parts.append("\n## 符合條件的詳細日誌\n")
    return "".join(parts)


def render_markdown_summary(counts, split_files=None) -> str:
    """渲染僅包含摘要的 Markdown 內容字串。"""
    parts = ["# Java Log 分析報告\n\n"]
    parts.append("## 統計摘要\n")
    parts.append("| 日誌等級 | 數量 |\n")
    parts.append("| --- | --- |\n")
    for level, count in sorted(counts.items()):
        parts.append(f"| {level} | {count} |\n")

    parts.append("\n## 詳細日誌已分割\n")
    parts.append("請參考下列檔案：\n")
    for file_path in split_files or []:
        parts.append(f"- `{file_path}`\n")
    return "".join(parts)


def serialize_markdown_log(index: int, log) -> str:
    """渲染單筆 Markdown 詳細區塊。"""
    parts = [f"### {index}. [{log['level']}] {log['message']}\n"]
    lines_str = ", ".join(map(str, normalize_line_numbers(log.get('line_numbers'))))
    parts.append(f"- **出現行號**: `{lines_str}`\n")

    if log['count'] > 1:
        parts.append(f"- **時間範圍**: `{log['timestamp']}` ～ `{log['last_timestamp']}`\n")
    else:
        parts.append(f"- **出現時間**: `{log['timestamp']}`\n")

    parts.append(f"- **線程**: `{log['thread']}`\n")
    parts.append(f"- **Logger**: `{log['logger']}`\n")
    parts.append(f"- **來源檔案**: `{log['filename']}`\n")

    if log.get('message_body'):
        parts.append("#### 延伸訊息:\n")
        parts.append("```text\n")
        parts.append(log['message_body'].rstrip())
        parts.append("\n```\n")

    if log['stacktrace']:
        parts.append("#### 內容/堆疊追蹤 (含原始行號):\n")
        parts.append("```java\n")
        parts.append(log['stacktrace'].strip())
        parts.append("\n```\n")

    parts.append("\n---\n")
    return "".join(parts)
