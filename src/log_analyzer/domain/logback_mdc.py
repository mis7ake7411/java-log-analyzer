"""Encode MDC extraction metadata in regex groups and decode matched values."""

from __future__ import annotations

import json
import re
from typing import Any

_GROUP_PREFIX = "mdc_"
_GROUP_NAME_RE = re.compile(r"^mdc_(\d+)_([0-9a-f]+)$")


def make_mdc_group_name(
    token_index: int,
    key: str | None,
    default: str | None,
    format_spec: str,
) -> str:
    """建立可還原單一 MDC Pattern token 資訊的正則群組名稱。

    Args:
        token_index: Pattern 中此 token 的索引，用於避免重複 token 撞名。
        key: 明確指定的 MDC key；None 表示輸出整份 MDC map。
        default: key 不存在時 Logback 輸出的替代值；未設定時為 None。
        format_spec: token 的最小／最大寬度格式字串。

    Returns:
        僅含合法字元且可由本模組解碼的具名群組名稱。
    """
    width_spec = format_spec.lstrip("-")
    width, separator, precision = width_spec.partition(".")
    metadata = {
        "key": key,
        "default": default,
        "min_width": int(width) if width else None,
        "max_width": int(precision) if separator and precision else None,
        "left_aligned": format_spec.startswith("-"),
    }
    encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{_GROUP_PREFIX}{token_index:05d}_{encoded.hex()}"


def is_mdc_capture_group(name: str) -> bool:
    """判斷正則群組名稱是否保留給 MDC 擷取使用。"""
    return name.startswith(_GROUP_PREFIX)


def extract_mdc(match: re.Match[str]) -> dict[str, str]:
    """從 Pattern 匹配結果還原可作為關聯鍵的 MDC key/value。

    keyed conversion 會忽略空值、等於明確 default 的值，以及長度碰到最大寬度
    的可能截斷值。裸 map 採標準逗號分隔語法；格式不完整時整份 map 不採用，
    同一 key 值衝突時只排除該 key。最小寬度輸出的對齊空白會依格式方向移除；
    原始值若剛好以空白填滿最小寬度，Logback 未提供足夠資訊判別是否為補位。

    Args:
        match: 由 compile_logback_pattern 編譯出的正則匹配結果。

    Returns:
        僅包含非空、未使用 default、未疑似截斷且無衝突的 MDC 字典。
    """
    values: dict[str, str] = {}
    conflicts: set[str] = set()

    for group_name, rendered_value in match.groupdict().items():
        metadata = _decode_metadata(group_name)
        if metadata is None or rendered_value is None:
            continue

        key = metadata["key"]
        if key is None:
            value = _remove_alignment_padding(rendered_value, metadata)
            if _may_be_truncated(value, metadata):
                continue
            pairs = _parse_bare_map(value)
            if pairs is None:
                continue
        else:
            value = _normalize_keyed_value(rendered_value, metadata)
            pairs = [] if value is None else [(key, value)]

        for pair_key, value in pairs:
            if not pair_key or not value:
                continue
            if pair_key in values and values[pair_key] != value:
                conflicts.add(pair_key)
            else:
                values[pair_key] = value

    return {key: value for key, value in values.items() if key not in conflicts}


def _decode_metadata(group_name: str) -> dict[str, Any] | None:
    match = _GROUP_NAME_RE.fullmatch(group_name)
    if match is None:
        return None
    try:
        metadata = json.loads(bytes.fromhex(match.group(2)).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    return metadata


def _normalize_keyed_value(rendered_value: str, metadata: dict[str, Any]) -> str | None:
    value = _remove_alignment_padding(rendered_value, metadata)
    if not value:
        return None
    if _may_be_truncated(value, metadata):
        return None
    if value == metadata["default"]:
        return None
    return value


def _may_be_truncated(value: str, metadata: dict[str, Any]) -> bool:
    maximum_width = metadata["max_width"]
    return maximum_width is not None and len(value) >= maximum_width


def _remove_alignment_padding(rendered_value: str, metadata: dict[str, Any]) -> str:
    minimum_width = metadata["min_width"]
    if minimum_width is None or len(rendered_value) != minimum_width:
        return rendered_value
    if metadata["left_aligned"]:
        return rendered_value.rstrip(" ")
    return rendered_value.lstrip(" ")


def _parse_bare_map(rendered_value: str) -> list[tuple[str, str]] | None:
    """Parse `key=value, key2=value2`; reject the whole map if a pair is unclear."""
    if not rendered_value:
        return []

    pairs: list[tuple[str, str]] = []
    for index, part in enumerate(rendered_value.split(",")):
        if index:
            part = part.lstrip(" ")
        if part.count("=") != 1:
            return None
        key, value = part.split("=", 1)
        if not key or any(character.isspace() for character in key):
            return None
        pairs.append((key, value))
    return pairs
