from __future__ import annotations

import json
import re
from collections.abc import Mapping


def format_occurrence_detail(occurrence: Mapping[str, object], *, markdown: bool = False) -> str:
    """組合來源座標與可選 MDC，讓各匯出格式保留相同的逐筆資訊。

    Args:
        occurrence: 含檔名、行號、時間、執行緒與可選 MDC 的出現紀錄。
        markdown: 是否以行內程式碼保護 MDC 的 Markdown 特殊字元。

    Returns:
        座標文字；沒有 MDC 時保持既有輸出，有 MDC 時附上排序後的 JSON。
    """
    detail = (
        f"{occurrence['filename']}:{occurrence['line_number']} | "
        f"{occurrence['timestamp']} | {occurrence['thread']}"
    )
    mdc = occurrence.get("mdc")
    if isinstance(mdc, Mapping) and mdc:
        serialized = json.dumps(
            dict(mdc),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if markdown:
            serialized = _markdown_code_span(serialized)
        detail += f" | MDC: {serialized}"
    return detail


def _markdown_code_span(value: str) -> str:
    longest_backtick_run = max(
        (len(run) for run in re.findall(r"`+", value)),
        default=0,
    )
    delimiter = "`" * (longest_backtick_run + 1)
    return f"{delimiter}{value}{delimiter}"
