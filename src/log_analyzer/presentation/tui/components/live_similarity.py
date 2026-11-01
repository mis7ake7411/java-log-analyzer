"""即時追蹤的選用相似訊息條件元件。"""

from __future__ import annotations

from dataclasses import fields
from math import isfinite

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Checkbox, Input, Label, Static

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.conditions import LiveAnalysisRequest
from log_analyzer.presentation.tui.components.inputs import ShortcutInput


class LiveSimilaritySettings(Vertical):
    """提供可選的即時相似訊息條件，並將條件建立交由呼叫端決定。

    元件只負責收集設定與通知條件變更，不會開始或重建追蹤工作。
    """

    class Changed(Message):
        """相似訊息條件已改變，供父層更新條件變更提示。"""

    def __init__(self, *, id: str = "live-similarity-settings") -> None:
        """建立預設關閉且收合的相似訊息設定。

        Args:
            id: 元件識別碼。
        """
        super().__init__(id=id)
        self._enabled = False
        self._reference = ""
        self._threshold_text = "80"
        self._locked = False

    def compose(self) -> ComposeResult:
        yield Checkbox("相似訊息", value=self._enabled, id="live-similarity-enabled")
        with Vertical(id="live-similarity-details"):
            yield Label("參考訊息")
            yield ShortcutInput(
                value=self._reference,
                placeholder="輸入要比對的訊息正文",
                id="live-similarity-reference",
            )
            yield Label("相似度門檻（0–100）")
            yield Input(
                value=self._threshold_text,
                placeholder="80",
                id="live-similarity-threshold",
            )
            yield Static(
                "比對正文、保留大小寫；與關鍵字／Level／時間取交集",
                id="live-similarity-hint",
                markup=False,
            )

    def on_mount(self) -> None:
        """讓初始展開狀態與目前設定同步。"""
        self._sync_controls()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """處理本元件的開關並通知父層條件已改變。

        Args:
            event: Textual 核取方塊變更事件。
        """
        if event.checkbox.id != "live-similarity-enabled":
            return
        event.stop()
        enabled = bool(event.value)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._sync_details()
        self.post_message(self.Changed())

    def on_input_changed(self, event: Input.Changed) -> None:
        """保留輸入文字，並在使用者編輯條件時通知父層。

        Args:
            event: Textual 輸入欄位變更事件。
        """
        if event.input.id == "live-similarity-reference":
            attribute = "_reference"
        elif event.input.id == "live-similarity-threshold":
            attribute = "_threshold_text"
        else:
            return

        event.stop()
        value = event.value
        if value == getattr(self, attribute):
            return
        setattr(self, attribute, value)
        self.post_message(self.Changed())

    def set_reference(self, reference: str) -> None:
        """填入參考文字並啟用相似條件，不啟動或重建追蹤。

        Args:
            reference: 用來比對新完成事件正文的參考文字。

        Raises:
            TypeError: `reference` 不是字串。
        """
        if not isinstance(reference, str):
            raise TypeError("reference must be a string")

        changed = self._reference != reference or not self._enabled
        self._reference = reference
        self._enabled = True
        if self.is_mounted:
            self.query_one("#live-similarity-reference", Input).value = reference
            self.query_one("#live-similarity-enabled", Checkbox).value = True
            self._sync_details()
        if changed:
            self.post_message(self.Changed())

    def set_locked(self, locked: bool) -> None:
        """鎖定或解鎖所有條件控制，不更動目前選項與輸入值。

        Args:
            locked: True 時停用開關、參考文字與門檻欄位。
        """
        self._locked = bool(locked)
        if self.is_mounted:
            self._sync_controls()

    def get_identity(self) -> tuple[str, float] | None:
        """回傳啟用中的有效參考文字與門檻，未啟用時回傳 None。

        Returns:
            穩定的 `(參考文字, 門檻)` tuple；條件未啟用時為 None。

        Raises:
            ValueError: 參考文字空白，或門檻不是有限的 0 到 100 數值。
        """
        if not self._enabled:
            return None
        if not self._reference.strip():
            raise ValueError("參考訊息不可空白")
        try:
            threshold = float(self._threshold_text.strip())
        except ValueError as exc:
            raise ValueError("相似度門檻必須是 0 到 100 的有限數字") from exc
        if not isfinite(threshold) or not 0 <= threshold <= 100:
            raise ValueError("相似度門檻必須是 0 到 100 的有限數字")
        return self._reference, threshold

    def as_request(self, base: AnalysisRequest) -> AnalysisRequest:
        """把有效條件附加到既有分析條件，未啟用時原樣回傳 base。

        Args:
            base: 已組好的來源、時間、關鍵字與 Level 等條件。

        Returns:
            相似條件關閉時原本的 `base`；啟用時為 `LiveAnalysisRequest`。

        Raises:
            TypeError: `base` 不是 `AnalysisRequest`。
            ValueError: 啟用中的參考文字或門檻無效。
        """
        if not isinstance(base, AnalysisRequest):
            raise TypeError("base must be an AnalysisRequest")
        identity = self.get_identity()
        if identity is None:
            return base

        base_values = {
            field.name: getattr(base, field.name)
            for field in fields(AnalysisRequest)
            if field.init
        }
        reference, threshold = identity
        return LiveAnalysisRequest(
            **base_values,
            similar_to=reference,
            similarity_threshold=threshold,
        )

    def _sync_controls(self) -> None:
        self.query_one("#live-similarity-enabled", Checkbox).disabled = self._locked
        self.query_one("#live-similarity-reference", Input).disabled = self._locked
        self.query_one("#live-similarity-threshold", Input).disabled = self._locked
        self._sync_details()

    def _sync_details(self) -> None:
        self.query_one("#live-similarity-details").display = self._enabled
