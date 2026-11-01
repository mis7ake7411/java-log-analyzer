"""Dashboard 專用 Rich 色票與卡片外觀。"""

from dataclasses import dataclass

from rich.console import RenderableType
from rich.panel import Panel
from rich.style import Style
from rich.text import Text


@dataclass(frozen=True)
class DashboardPalette:
    """讓 Rich 摘要與 Textual 結果框使用同一組固定色彩。"""

    background: str = "#111827"
    border: str = "#334155"
    title: str = "#F8FAFC"
    value: str = "#E2E8F0"
    secondary: str = "#CBD5E1"
    success: str = "#4ADE80"
    warning: str = "#FBBF24"
    error: str = "#F87171"
    link: str = "#38BDF8"

    @property
    def panel_style(self) -> Style:
        return Style(color=self.value, bgcolor=self.background)

    @property
    def border_style(self) -> Style:
        return Style(color=self.border)

    @property
    def title_style(self) -> Style:
        return Style(color=self.title, bold=True)

    @property
    def value_style(self) -> Style:
        return Style(color=self.value)

    @property
    def value_bold_style(self) -> Style:
        return Style(color=self.value, bold=True)

    @property
    def secondary_style(self) -> Style:
        return Style(color=self.secondary)

    @property
    def secondary_bold_style(self) -> Style:
        return Style(color=self.secondary, bold=True)

    @property
    def success_style(self) -> Style:
        return Style(color=self.success, bold=True)

    @property
    def warning_style(self) -> Style:
        return Style(color=self.warning, bold=True)

    @property
    def error_style(self) -> Style:
        return Style(color=self.error, bold=True)

    def interactive_style(self, action: str) -> Style:
        """為程式產生的 dashboard 操作保留 Rich click metadata。"""
        return Style(
            color=self.link,
            underline=True,
            meta={"@click": action},
        )

    @property
    def css_variables(self) -> dict[str, str]:
        """提供結果框使用的 TCSS 色彩變數。"""
        return {
            "dashboard-background": self.background,
            "dashboard-border": self.border,
            "dashboard-value": self.value,
        }


DASHBOARD_PALETTE = DashboardPalette()


def dashboard_card(
    content: RenderableType,
    title: str | Text,
    *,
    padding: tuple[int, int] = (0, 1),
    expand: bool = True,
    palette: DashboardPalette = DASHBOARD_PALETTE,
) -> Panel:
    """建立外觀一致、可獨立交給 Rich Console 繪製的卡片。"""
    title_text = (
        Text(title, style=palette.title_style) if isinstance(title, str) else title
    )
    return Panel(
        content,
        title=title_text,
        border_style=palette.border_style,
        style=palette.panel_style,
        padding=padding,
        expand=expand,
    )
