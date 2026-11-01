"""檢視即時追蹤已保存的單筆事件。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from log_analyzer.application.events.similarity import message_text


class LiveEventScreen(ModalScreen[None]):
    """以原始保存內容顯示單筆追蹤事件，不讀取來源檔案。"""

    BINDINGS = [("escape", "dismiss", "關閉")]

    class UseAsSimilarityReference(Message):
        """要求將保存事件的正文帶入即時相似訊息條件。"""

        def __init__(self, reference: str) -> None:
            self.reference = reference
            super().__init__()

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
            reference_status = Static(
                "",
                id="live-event-reference-status",
                markup=False,
                classes="field-hint",
            )
            reference_status.display = False
            yield reference_status
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
            yield Button(
                "帶入相似參考",
                id="live-event-use-as-similarity-reference",
            )
            yield Button("關閉", id="live-event-close", variant="primary")

    def show_reference_notice(self, message: str) -> None:
        """在事件檢視器內顯示參考條件操作提示。

        Args:
            message: 要顯示給目前使用者的操作說明。
        """
        status = self.query_one("#live-event-reference-status", Static)
        status.update(message)
        status.display = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "live-event-close":
            event.stop()
            self.dismiss()
        elif event.button.id == "live-event-use-as-similarity-reference":
            event.stop()
            self.post_message(self.UseAsSimilarityReference(message_text(self._event)))
