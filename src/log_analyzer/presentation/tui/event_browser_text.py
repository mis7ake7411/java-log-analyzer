from __future__ import annotations

from collections.abc import Mapping

from rich.text import Text

STACKTRACE_PREVIEW_LINES = 8


def representative_message(group: Mapping[str, object]) -> str:
    """取得分析保留的群組代表訊息，不將佔位文字當成原文。

    Args:
        group: 分析結果中保留的事件群組。

    Returns:
        非空的本文或首行訊息；兩者皆無時回傳空字串。
    """
    message = group.get("message_body")
    if not isinstance(message, str) or not message:
        message = group.get("message")
    return message if isinstance(message, str) else ""


def retained_stacktrace(group: Mapping[str, object]) -> str:
    """取得分析結果保留的完整代表堆疊。

    Args:
        group: 分析結果中保留的事件群組。

    Returns:
        完整堆疊字串；沒有字串堆疊時依序接合保留的堆疊行。
    """
    stacktrace = group.get("stacktrace")
    if isinstance(stacktrace, str) and stacktrace:
        return stacktrace
    stacktrace_lines = group.get("stacktrace_lines")
    return (
        "\n".join(line for line in stacktrace_lines if isinstance(line, str))
        if isinstance(stacktrace_lines, list)
        else ""
    )


def visible_stacktrace(stacktrace: str, expanded: bool) -> tuple[str, bool]:
    """依展開狀態產生堆疊預覽，並回報是否需要展開控制。

    Args:
        stacktrace: 完整保留堆疊。
        expanded: 是否顯示完整堆疊。

    Returns:
        預覽文字與是否應顯示展開／收合控制。
    """
    if not stacktrace.strip():
        return "", False
    lines = stacktrace.splitlines()
    has_toggle = len(lines) > STACKTRACE_PREVIEW_LINES
    if has_toggle and not expanded:
        return "\n".join(lines[:STACKTRACE_PREVIEW_LINES]), True
    return stacktrace, has_toggle


def highlight_literal(
    text: Text | str,
    keyword: str | None,
    ignore_case: bool = False,
) -> Text:
    """以 literal 比對醒目標示文字中的關鍵字，不解析標記語法。

    Args:
        text: 原始字串或已含樣式的 Rich 文字。
        keyword: 要醒目標示的字面內容；空值不套用標示。
        ignore_case: 是否使用 Unicode casefold 忽略大小寫。

    Returns:
        保留原有樣式並加入關鍵字醒目樣式的文字副本。
    """
    highlighted = text.copy() if isinstance(text, Text) else Text(text)
    if not keyword:
        return highlighted

    plain = highlighted.plain
    if ignore_case:
        folded_characters: list[str] = []
        original_starts: list[int] = []
        original_ends: list[int] = []
        for index, character in enumerate(plain):
            folded = character.casefold()
            folded_characters.append(folded)
            original_starts.extend([index] * len(folded))
            original_ends.extend([index + 1] * len(folded))
        haystack = "".join(folded_characters)
        needle = keyword.casefold()
        if not needle:
            return highlighted
        cursor = 0
        while (found := haystack.find(needle, cursor)) >= 0:
            start = original_starts[found]
            end = original_ends[found + len(needle) - 1]
            highlighted.stylize("bold black on yellow", start, end)
            cursor = found + len(needle)
        return highlighted

    cursor = 0
    while (found := plain.find(keyword, cursor)) >= 0:
        end = found + len(keyword)
        highlighted.stylize("bold black on yellow", found, end)
        cursor = end
    return highlighted
