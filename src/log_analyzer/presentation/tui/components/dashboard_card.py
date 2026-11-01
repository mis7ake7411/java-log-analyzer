"""Rich Dashboard shared card factory."""

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text

from log_analyzer.presentation.tui.dashboard_styles import (
    DASHBOARD_CATEGORY_APPEARANCES,
    DASHBOARD_PALETTE,
    DashboardCardCategory,
    DashboardPalette,
)


def dashboard_card(  # noqa: PLR0913 - explicit independent Rich card options stay keyword-only
    content: RenderableType,
    title: str | Text,
    *,
    padding: tuple[int, int] = (0, 1),
    expand: bool = True,
    palette: DashboardPalette = DASHBOARD_PALETTE,
    category: DashboardCardCategory | None = None,
) -> Panel:
    """建立外觀一致、可獨立交給 Rich Console 繪製的卡片。"""
    appearance = DASHBOARD_CATEGORY_APPEARANCES.get(category) if category else None
    title_style = appearance.title_style if appearance else palette.title_style
    border_style = appearance.border_style if appearance else palette.border_style
    if isinstance(title, str):
        title_text = Text(title, style=title_style)
    elif appearance is None:
        title_text = title
    else:
        title_text = title.copy()
        if title_text:
            title_text.stylize(title_style, 0, len(title_text))
    return Panel(
        content,
        title=title_text,
        border_style=border_style,
        style=palette.panel_style,
        padding=padding,
        expand=expand,
    )
