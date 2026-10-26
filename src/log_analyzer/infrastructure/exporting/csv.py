from __future__ import annotations

import csv
from io import StringIO

from ...domain.parsing.aggregation import normalize_line_numbers
from .fields import EXPORT_FIELDS, EXPORT_FIELD_LABELS, normalize_export_fields


def render_csv_report(counts, matched_logs, fields=None) -> str:
    """將分析結果渲染為 CSV 內容字串"""
    parts = [render_csv_prefix(counts, fields)]
    for log in matched_logs:
        parts.append(serialize_csv_log(log, fields))
    return "".join(parts)


def render_csv_prefix(counts, fields=None) -> str:
    """渲染 CSV 報表前綴，不包含詳細列資料"""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['--- 統計摘要 ---'])
    writer.writerow(['日誌等級 (Level)', '出現次數 (Count)'])
    for level, count in sorted(counts.items()):
        writer.writerow([level, count])
    writer.writerow([])

    writer.writerow(['--- 詳細日誌內容 (已合併重複項) ---'])
    if fields is None:
        detail_headers = [
            '出現次數', '出現行號', '第一次時間', '最後一次時間', '等級', '線程',
            'Logger', '來源檔案', '訊息內容', '延伸訊息', '堆疊追蹤 (含行號)', '事件座標',
        ]
    else:
        detail_headers = [
            EXPORT_FIELD_LABELS[field]
            for field in normalize_export_fields(fields)
        ]
    writer.writerow(detail_headers)
    return buffer.getvalue()


def render_csv_summary(counts, split_files=None) -> str:
    """渲染僅包含摘要的 CSV 內容字串"""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['--- 統計摘要 ---'])
    writer.writerow(['日誌等級 (Level)', '出現次數 (Count)'])
    for level, count in sorted(counts.items()):
        writer.writerow([level, count])

    writer.writerow([])
    writer.writerow(['--- 詳細日誌已分割 ---'])
    writer.writerow(['請參考下列檔案'])
    for file_path in split_files or []:
        writer.writerow([file_path])
    return buffer.getvalue()


def serialize_csv_log(log, fields=None) -> str:
    """渲染單筆 CSV 詳細列"""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_csv_log_row(log, fields))
    return buffer.getvalue()


def _csv_log_row(log, fields=None) -> list[str]:
    selected_fields = (
        EXPORT_FIELDS if fields is None else normalize_export_fields(fields)
    )
    return [_csv_field_value(log, field) for field in selected_fields]


def _csv_field_value(log, field: str):
    if field == "line_numbers":
        return ", ".join(map(str, normalize_line_numbers(log.get("line_numbers"))))
    if field == "message_body":
        return log.get("message_body", "").strip()
    if field == "stacktrace":
        return log["stacktrace"].strip()
    if field == "occurrences":
        return "\n".join(
            f"{occurrence['filename']}:{occurrence['line_number']} | "
            f"{occurrence['timestamp']} | {occurrence['thread']}"
            for occurrence in log.get("occurrences", [])
        )
    return log[field]
