"""供測試掛載正式 TUI 元件與畫面的輕量 Textual 容器。"""

from textual.app import App, ComposeResult
from textual.widgets import Static

from log_analyzer.presentation.tui.dashboard_styles import DASHBOARD_PALETTE
from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.components.time_range import TimeRangeSettings
from log_analyzer.presentation.tui.composition import ViewCompositionMixin


class StyledApp(App[None]):
    """以正式結果框色票解析共用 TCSS 的測試應用程式基底。"""

    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(DASHBOARD_PALETTE.css_variables)
        return variables


class AdvancedSettingsHarness(ViewCompositionMixin, StyledApp):
    """直接組裝正式進階與匯出設定，不載入應用控制器。"""

    def compose(self) -> ComposeResult:
        self._auto_output_name = "analysis_test"
        yield from self._compose_advanced_settings()
        yield from self._compose_export_settings()


class TimeRangeSettingsHarness(StyledApp):
    """以應用程式樣式掛載正式時間範圍元件。"""

    def compose(self) -> ComposeResult:
        yield TimeRangeSettings()


class RecipeActionsHarness(ViewCompositionMixin, StyledApp):
    """直接組裝正式操作按鈕，不載入分析流程。"""

    def compose(self) -> ComposeResult:
        yield from self._compose_actions()


class ScreenHarness(StyledApp):
    """以應用程式樣式承載正式 Screen。"""

    def compose(self) -> ComposeResult:
        yield Static("", id="screen-test-host")
