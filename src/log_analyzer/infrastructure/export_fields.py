from __future__ import annotations

from collections.abc import Iterable

EXPORT_FIELDS = (
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
    "occurrences",
)

EXPORT_FIELD_LABELS = {
    "count": "出現次數",
    "line_numbers": "行號",
    "timestamp": "首次時間",
    "last_timestamp": "最後時間",
    "level": "層級",
    "thread": "執行緒",
    "logger": "記錄器",
    "filename": "檔案名稱",
    "message": "訊息",
    "message_body": "訊息本文",
    "stacktrace": "堆疊追蹤",
    "occurrences": "發生位置",
}


def normalize_export_fields(fields: Iterable[str] | None) -> tuple[str, ...]:
    """驗證欄位選擇，並依匯出格式共用的順序回傳唯一欄位。"""
    if fields is None:
        return EXPORT_FIELDS

    selected = set(fields)
    if not selected:
        raise ValueError("至少要選擇一個匯出欄位")

    unknown = selected.difference(EXPORT_FIELDS)
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise ValueError(f"未知的匯出欄位：{names}")

    return tuple(field for field in EXPORT_FIELDS if field in selected)
