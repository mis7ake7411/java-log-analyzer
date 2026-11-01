"""顯示追蹤摘要固定條件與來源缺口的可捲動檢視畫面。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class LiveSummaryDetailsScreen(ModalScreen[None]):
    """讓窄版摘要以獨立視窗閱讀完整條件、擷取時間與缺口資訊。"""

    BINDINGS = [("escape", "dismiss", "關閉")]

    def __init__(self, details: str) -> None:
        super().__init__()
        self._details = details

    def compose(self) -> ComposeResult:
        with Vertical(id="live-summary-details-dialog"):
            yield Static("追蹤條件與缺口", classes="section-title")
            with ScrollableContainer(id="live-summary-details-scroll"):
                yield Static(
                    self._details,
                    id="live-summary-details-content",
                    markup=False,
                )
            yield Button("關閉", id="live-summary-details-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "live-summary-details-close":
            event.stop()
            self.dismiss()
