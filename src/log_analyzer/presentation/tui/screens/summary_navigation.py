from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option
from rich.text import Text

from log_analyzer.presentation.dashboard_navigation import DashboardTarget


class SummaryNavigationScreen(ModalScreen[int | None]):
    """提供與摘要滑鼠入口相同的鍵盤導覽。"""

    BINDINGS = [
        Binding("escape", "close", "關閉"),
        Binding("d,c,a,ctrl+x,enter", "ignore", show=False),
    ]
    DEFAULT_CSS = """
    SummaryNavigationScreen { align: center middle; }
    #summary-navigation-card {
        width: 90%; max-width: 100; min-width: 0;
        height: 80%; min-height: 10; padding: 0 1;
        border: round #38bdf8; background: #0f172a;
    }
    #summary-navigation-title { height: 1; text-style: bold; }
    #summary-options { height: 1fr; min-height: 0; }
    #close-summary-navigation { height: 3; width: 100%; }
    """

    def __init__(self, targets: tuple[DashboardTarget, ...]) -> None:
        """固定這次導覽的入口清單。

        Args:
            targets: 目前保留結果的 Level 與高頻事件入口。
        """
        super().__init__()
        self.targets = targets

    def compose(self) -> ComposeResult:
        """建立可捲動的入口清單與固定關閉按鈕。

        Returns:
            Textual 要掛載的導覽元件。
        """
        with Container(id="summary-navigation-card"):
            yield Static(
                "摘要導覽 · ↑↓ 選擇，Enter 查看",
                id="summary-navigation-title",
                markup=False,
            )
            yield OptionList(
                *(
                    Option(Text(target.label), id=str(index))
                    for index, target in enumerate(self.targets)
                ),
                id="summary-options",
            )
            yield Button("關閉", id="close-summary-navigation")

    def on_mount(self) -> None:
        self.query_one("#summary-options", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """將選取索引交回主畫面，由主畫面確認結果仍有效。

        Args:
            event: 清單的選取事件。

        Returns:
            None。
        """
        event.stop()
        if event.option_list.id == "summary-options":
            self.dismiss(int(event.option.id))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-summary-navigation":
            event.stop()
            self.action_close()

    def action_close(self) -> None:
        """取消導覽，不套用任何篩選條件。"""
        self.dismiss(None)

    def action_ignore(self) -> None:
        """攔截主畫面的快捷鍵，避免離開導覽或更動原結果。"""
