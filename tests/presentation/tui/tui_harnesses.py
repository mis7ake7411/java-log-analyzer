"""供測試掛載正式 TUI 元件與畫面的輕量 Textual 容器。"""

from textual.app import App, ComposeResult
from textual.widgets import Static

from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.components.time_range import TimeRangeSettings
from log_analyzer.presentation.tui.composition import ViewCompositionMixin


class AdvancedSettingsHarness(ViewCompositionMixin, App):
    """直接組裝正式進階與匯出設定，不載入應用控制器。"""

    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def compose(self) -> ComposeResult:
        self._auto_output_name = "analysis_test"
        yield from self._compose_advanced_settings()
        yield from self._compose_export_settings()


class TimeRangeSettingsHarness(App):
    """以應用程式樣式掛載正式時間範圍元件。"""

    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def compose(self) -> ComposeResult:
        yield TimeRangeSettings()


class RecipeActionsHarness(ViewCompositionMixin, App):
    """直接組裝正式操作按鈕，不載入分析流程。"""

    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def compose(self) -> ComposeResult:
        yield from self._compose_actions()


class ScreenHarness(App):
    """以應用程式樣式承載正式 Screen。"""

    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def compose(self) -> ComposeResult:
        yield Static("", id="screen-test-host")
