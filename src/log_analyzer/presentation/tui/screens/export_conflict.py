from __future__ import annotations
from collections.abc import Iterable
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from textual.containers import VerticalScroll


class ExportConflictScreen(ModalScreen[str | None]):
    """列出匯出衝突路徑，讓使用者選擇改名、覆蓋或取消。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, paths: Iterable[str]) -> None:
        self.paths = tuple(paths)
        super().__init__()

    def compose(self):
        with Container(id="export-conflict"), Container(id="export-conflict-card"):
            yield Static("輸出檔案已存在", id="export-conflict-title")
            yield Static(
                "請改名、確認覆蓋，或取消本次匯出。",
                id="export-conflict-message",
                markup=False,
            )
            with VerticalScroll(id="export-conflict-scroll"):
                yield Static(
                    "\n".join(self.paths),
                    id="export-conflict-paths",
                    markup=False,
                )
            with Container(id="export-conflict-actions"):
                yield Button("改名", id="export-conflict-rename")
                yield Button("覆蓋", variant="warning", id="export-conflict-overwrite")
                yield Button("取消", id="export-conflict-cancel")

    def on_mount(self) -> None:
        self.query_one("#export-conflict-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "export-conflict-rename": "rename",
            "export-conflict-overwrite": "overwrite",
            "export-conflict-cancel": None,
        }
        if event.button.id in actions:
            self.dismiss(actions[event.button.id])
