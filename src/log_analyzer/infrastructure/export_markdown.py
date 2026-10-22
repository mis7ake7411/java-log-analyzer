from __future__ import annotations

from ..domain.parser_aggregation import normalize_line_numbers
from .export_fields import EXPORT_FIELD_LABELS, normalize_export_fields


def render_markdown_report(counts, matched_logs, fields=None) -> str:
    """將分析結果渲染為 Markdown 內容字串"""
    selected_fields = None if fields is None else normalize_export_fields(fields)
    parts = [render_markdown_prefix(counts, selected_fields)]
    if selected_fields is not None and matched_logs:
        for i, log in enumerate(matched_logs, 1):
            parts.append(serialize_markdown_log(i, log, selected_fields))
        return "".join(parts)
    if not matched_logs:
        parts.append("無符合條件的記錄\n")
    else:
        for i, log in enumerate(matched_logs, 1):
            parts.append(serialize_markdown_log(i, log))
    return "".join(parts)


def render_markdown_prefix(counts, fields=None) -> str:
    """渲染 Markdown 報表前綴，不包含詳細記錄"""
    if fields is not None:
        normalize_export_fields(fields)
    parts = ["# Java Log 分析報告\n\n"]
    parts.append("## 統計摘要\n")
    parts.append("| 日誌等級 | 數量 |\n")
    parts.append("| --- | --- |\n")
    for level, count in sorted(counts.items()):
        parts.append(f"| {level} | {count} |\n")

    parts.append("\n## 符合條件的詳細日誌\n")
    return "".join(parts)


def render_markdown_summary(counts, split_files=None) -> str:
    """渲染僅包含摘要的 Markdown 內容字串"""
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


def serialize_markdown_log(index: int, log, fields=None) -> str:
    """渲染單筆 Markdown 詳細區塊"""
    if fields is not None:
        return _serialize_selected_markdown_log(
            index, log, normalize_export_fields(fields)
        )
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
    parts.append("- **事件座標**:\n")
    for occurrence in log.get('occurrences', []):
        parts.append(
            f"  - {occurrence['filename']}:{occurrence['line_number']} | "
            f"{occurrence['timestamp']} | {occurrence['thread']}\n"
        )

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


def _serialize_selected_markdown_log(index: int, log, fields: tuple[str, ...]) -> str:
    parts = [_markdown_log_heading(index, log, fields)]
    parts.extend(
        _render_markdown_field(field, log)
        for field in fields
        if field not in {"level", "message"}
    )
    parts.append("\n---\n")
    return "".join(parts)


def _markdown_log_heading(index: int, log, fields: tuple[str, ...]) -> str:
    includes_level = "level" in fields
    includes_message = "message" in fields
    if includes_level and includes_message:
        return f"### {index}. [{log['level']}] {log['message']}\n"
    if includes_level:
        return f"### {index}. [{log['level']}]\n"
    if includes_message:
        return f"### {index}. {log['message']}\n"
    return f"### {index}. 記錄\n"


def _render_markdown_field(field: str, log) -> str:
    label = EXPORT_FIELD_LABELS[field]
    if field == "line_numbers":
        lines_str = ", ".join(map(str, normalize_line_numbers(log.get("line_numbers"))))
        return f"- **{label}**: `{lines_str}`\n"
    if field in {"message_body", "stacktrace"}:
        return _render_markdown_content_field(field, label, log)
    if field == "occurrences":
        return _render_markdown_occurrences(label, log)
    return f"- **{label}**: `{log[field]}`\n"


def _render_markdown_content_field(field: str, label: str, log) -> str:
    language = "text" if field == "message_body" else "java"
    content = log.get(field, "")
    content = str(content).rstrip() if field == "message_body" else str(content).strip()
    return f"#### {label}:\n```{language}\n{content}\n```\n"


def _render_markdown_occurrences(label: str, log) -> str:
    occurrences = log.get("occurrences", [])
    if not occurrences:
        return f"- **{label}**:\n  - 無\n"
    details = "".join(
        f"  - {occurrence['filename']}:{occurrence['line_number']} | "
        f"{occurrence['timestamp']} | {occurrence['thread']}\n"
        for occurrence in occurrences
    )
    return f"- **{label}**:\n{details}"
