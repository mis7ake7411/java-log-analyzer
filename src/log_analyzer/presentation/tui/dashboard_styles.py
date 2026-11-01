"""Dashboard 專用 Rich 色票與卡片外觀。"""

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from types import MappingProxyType

from rich.style import Style


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
    info: str = "#93C5FD"
    debug: str = "#C4B5FD"
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

    @property
    def info_style(self) -> Style:
        return Style(color=self.info, bold=True)

    @property
    def debug_style(self) -> Style:
        return Style(color=self.debug, bold=True)

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


class DashboardCardCategory(str, Enum):
    SUMMARY = "summary"
    CONTEXT = "context"
    DISTRIBUTION = "distribution"
    EVENTS = "events"


@dataclass(frozen=True, slots=True)
class DashboardCategoryAppearance:
    title: str
    border: str

    @property
    def title_style(self) -> Style:
        return Style(color=self.title, bold=True)

    @property
    def border_style(self) -> Style:
        return Style(color=self.border)


DASHBOARD_CATEGORY_APPEARANCES: Mapping[
    DashboardCardCategory, DashboardCategoryAppearance
] = MappingProxyType(
    {
        DashboardCardCategory.SUMMARY: DashboardCategoryAppearance(
            title="#7DD3FC",
            border="#365B70",
        ),
        DashboardCardCategory.CONTEXT: DashboardCategoryAppearance(
            title="#C4B5FD",
            border="#57506E",
        ),
        DashboardCardCategory.DISTRIBUTION: DashboardCategoryAppearance(
            title="#5EEAD4",
            border="#2F6260",
        ),
        DashboardCardCategory.EVENTS: DashboardCategoryAppearance(
            title="#FCD34D",
            border="#6B5937",
        ),
    }
)
