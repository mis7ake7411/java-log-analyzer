"""檢視即時追蹤已保存的單筆事件。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class LiveEventScreen(ModalScreen[None]):
    """以原始保存內容顯示單筆追蹤事件，不讀取來源檔案。"""

    BINDINGS = [("escape", "dismiss", "關閉")]

    def __init__(self, event: dict[str, Any]) -> None:
        super().__init__()
        self._event = deepcopy(event)

    def compose(self) -> ComposeResult:
        event = self._event
        metadata = (
            ("來源檔", event.get("filename", "未提供")),
            ("來源世代", event.get("generation", "未提供")),
            ("起始行", event.get("line_number", event.get("line_num", "未提供"))),
            ("接收序號", event.get("sequence", "未提供")),
            ("時間", event.get("timestamp", "未提供")),
            ("Level", event.get("level", "未提供")),
            ("Thread", event.get("thread", "未提供")),
            ("Logger", event.get("logger", "未提供")),
        )
        raw_text = event.get("raw_text", event.get("full_text", ""))
        mdc = event.get("mdc")
        if isinstance(mdc, dict) and mdc:
            mdc_text = "\n".join(f"{key}：{value}" for key, value in mdc.items())
        else:
            mdc_text = "（無 MDC）" if not mdc else str(mdc)
        with Vertical(id="live-event-dialog"):
            yield Static("即時追蹤事件", classes="section-title")
            with ScrollableContainer(id="live-event-content"):
                yield Static("原始內容", classes="field-label")
                yield Static(str(raw_text), id="live-event-raw", markup=False)
                yield Static("事件中繼資料", classes="field-label")
                yield Static(
                    "\n".join(f"{label}：{value}" for label, value in metadata),
                    id="live-event-metadata",
                    markup=False,
                )
                yield Static("MDC", classes="field-label")
                yield Static(mdc_text, id="live-event-mdc", markup=False)
            yield Button("關閉", id="live-event-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "live-event-close":
            event.stop()
            self.dismiss()
