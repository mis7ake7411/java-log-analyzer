"""組裝 Dashboard 的識別文字、數值與操作樣式。"""

import re

from rich.style import Style
from rich.text import Text

from log_analyzer.presentation.tui.dashboard_styles import (
    DASHBOARD_CATEGORY_APPEARANCES,
    DASHBOARD_PALETTE,
    DashboardCardCategory,
)

_EXCEPTION_TYPE_TOKEN = re.compile(
    r"(?<![\w.$])[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?![\w.$])"
)


def dashboard_identifier_style(category: DashboardCardCategory) -> Style:
    """以卡片分類色辨識名稱與其他識別欄位。"""
    return DASHBOARD_CATEGORY_APPEARANCES[category].title_style


def dashboard_identifier_text(value: str, category: DashboardCardCategory) -> Text:
    """將識別文字視為純文字，套用所屬卡片分類色。"""
    return Text(str(value), style=dashboard_identifier_style(category))


def dashboard_action_style(base_style: Style, action: str | None) -> Style:
    """只附加點擊中繼資料，不改變資料文字的顏色與字型樣式。"""
    if action:
        return base_style + Style(meta={"@click": action})
    return base_style


def dashboard_level_style(level: str, action: str | None = None) -> Style:
    """依照 Level 套用語意色，未知 Level 保留一般數值色。"""
    level_name = str(level).upper()
    if level_name == "ERROR":
        base_style = DASHBOARD_PALETTE.error_style
    elif level_name == "WARN":
        base_style = DASHBOARD_PALETTE.warning_style
    elif level_name == "INFO":
        base_style = DASHBOARD_PALETTE.info_style
    elif level_name == "DEBUG":
        base_style = DASHBOARD_PALETTE.debug_style
    else:
        base_style = DASHBOARD_PALETTE.value_style
    return dashboard_action_style(base_style, action)


def dashboard_number_with_unit(
    value: int | str,
    unit: str,
    *,
    action: str | None = None,
) -> Text:
    """以主要數值色呈現數量，並以次要色呈現單位。"""
    text = Text(
        str(value),
        style=dashboard_action_style(DASHBOARD_PALETTE.value_style, action),
    )
    text.append(
        f" {unit}",
        style=dashboard_action_style(DASHBOARD_PALETTE.secondary_style, action),
    )
    return text


def dashboard_exception_text(signature: str) -> Text:
    """將例外類型 token 染紅，其餘簽章文字保留中性數值色。"""
    text = Text(str(signature), style=DASHBOARD_PALETTE.value_style)
    for match in _EXCEPTION_TYPE_TOKEN.finditer(text.plain):
        type_name = match.group().rsplit(".", 1)[-1]
        if type_name.endswith(("Exception", "Error", "Throwable")):
            text.stylize(DASHBOARD_PALETTE.error_style, match.start(), match.end())
    return text
