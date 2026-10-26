from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Container
from textual.events import Resize
from textual.message import Message
from textual.widgets import Button, Static


_CONDITION_KEYS = ("files", "levels", "time", "keyword", "pattern")
_CLEAR_BUTTON_PREFIX = "clear-condition-"


@dataclass(frozen=True)
class ConditionItem:
    """目前條件列的唯讀資料。

    Attributes:
        key: 固定條件識別碼。
        label: 條件顯示名稱。
        value: 條件目前值。
        can_clear: 是否允許清除此條件。
        clear_label: 清除按鈕文字。
    """

    key: str
    label: str
    value: str
    can_clear: bool = False
    clear_label: str = "清除"


_DEFAULT_ITEMS = (
    ConditionItem("files", "Files", "未選擇"),
    ConditionItem("levels", "Level", "全部"),
    ConditionItem("time", "Time", "不限"),
    ConditionItem("keyword", "Keyword", "未設定"),
    ConditionItem("pattern", "Pattern", "未設定"),
)


class ConditionsOverview(Container):
    """呈現目前五種分析條件，並轉送單一條件的清除請求。"""

    DEFAULT_CSS = """
    ConditionsOverview {
        width: 1fr;
        height: auto;
        min-width: 0;
        min-height: 0;
        margin-bottom: 1;
        padding: 0 1;
        border: round #334155;
        background: #0b1220;
    }

    #condition-overview-title {
        width: 1fr;
        height: 1;
        color: #e2e8f0;
        text-style: bold;
    }

    #condition-rows {
        width: 1fr;
        height: auto;
        min-width: 0;
        min-height: 0;
        layout: grid;
        grid-size: 1 5;
        grid-columns: 1fr;
        grid-rows: auto auto auto auto auto;
        grid-gutter: 0 1;
    }

    #condition-rows.wide {
        grid-size: 2 3;
        grid-columns: 1fr 1fr;
        grid-rows: auto auto auto;
    }

    .condition-row {
        width: 1fr;
        height: auto;
        min-width: 0;
        min-height: 1;
        margin: 0;
        padding: 0;
        layout: horizontal;
    }

    .condition-row Static {
        width: 1fr;
        height: auto;
        min-width: 0;
        min-height: 1;
        color: #cbd5e1;
        text-wrap: wrap;
    }

    #condition-rows Button.condition-clear {
        width: auto;
        min-width: 4;
        max-width: 12;
        height: 1;
        min-height: 1;
        margin: 0;
        padding: 0;
        border: none;
        color: #38bdf8;
        background: #17283b;
    }

    #condition-rows Button.condition-clear:focus {
        color: #f8fafc;
        background: #263c55;
        text-style: bold underline;
    }

    #condition-rows Button.condition-clear:disabled {
        color: #64748b;
        background: transparent;
    }
    """

    class ClearRequested(Message):
        """要求外層清除指定條件的訊息。

        Attributes:
            key: 要清除的固定條件 key。
        """

        def __init__(self, key: str) -> None:
            """建立一筆清除請求。

            Args:
                key: 要清除的固定條件 key。
            """
            self.key = key
            super().__init__()

    def __init__(self, *, id: str = "condition-overview") -> None:
        """建立含五種佔位條件的元件。

        Args:
            id: 根容器識別碼，供父畫面查詢。
        """
        super().__init__(id=id)
        self._items = {item.key: item for item in _DEFAULT_ITEMS}

    def compose(self) -> ComposeResult:
        """組合標題、五列條件摘要與各自的清除按鈕。

        Returns:
            Textual 要掛載的條件摘要元件。
        """
        yield Static("目前條件", id="condition-overview-title", markup=False)
        with Container(id="condition-rows"):
            for key in _CONDITION_KEYS:
                item = self._items[key]
                summary = f"{item.label}：{item.value}"
                with Container(id=f"condition-row-{key}", classes="condition-row"):
                    summary_widget = Static(
                        summary,
                        id=f"condition-{key}",
                        markup=False,
                    )
                    summary_widget.tooltip = summary
                    yield summary_widget
                    yield Button(
                        item.clear_label,
                        id=f"{_CLEAR_BUTTON_PREFIX}{key}",
                        classes="condition-clear",
                        disabled=not item.can_clear,
                        tooltip=f"{item.clear_label}：{item.label}",
                    )

    def on_mount(self) -> None:
        self._sync_column_layout(self.region.width)

    def on_resize(self, _event: Resize) -> None:
        """依元件完成的尺寸變更同步欄數。

        Args:
            _event: Textual 傳入的尺寸變更事件。
        """
        if self.is_mounted:
            self._sync_column_layout(self.region.width)

    def _sync_column_layout(self, width: int) -> None:
        """依元件的實際寬度切換單欄或雙欄排列。

        Args:
            width: 元件目前的可用寬度（終端儲存格數）。
        """
        rows = self.query_one("#condition-rows", Container)
        wide = "wide" in rows.classes
        if width >= 60 and not wide:
            rows.add_class("wide")
        elif width < 60 and wide:
            rows.remove_class("wide")

    def set_items(self, items: Sequence[ConditionItem]) -> None:
        """同步更新五列內容與清除狀態，保留目前元件與焦點。

        Args:
            items: 依 key 指定的五種目前條件。

        Returns:
            None。

        Raises:
            ValueError: items 未各自包含五個固定條件 key。
        """
        if len(items) != len(_CONDITION_KEYS):
            raise ValueError("items 必須各含 files、levels、time、keyword、pattern 條件")
        items_by_key = {item.key: item for item in items}
        if len(items_by_key) != len(_CONDITION_KEYS) or set(items_by_key) != set(
            _CONDITION_KEYS
        ):
            raise ValueError("items 必須各含 files、levels、time、keyword、pattern 條件")

        self._items = items_by_key
        if not self.is_mounted:
            return

        for key in _CONDITION_KEYS:
            item = items_by_key[key]
            summary = f"{item.label}：{item.value}"
            summary_widget = self.query_one(f"#condition-{key}", Static)
            summary_widget.update(summary)
            summary_widget.tooltip = summary

            clear_button = self.query_one(
                f"#{_CLEAR_BUTTON_PREFIX}{key}", Button
            )
            clear_button.label = item.clear_label
            clear_button.disabled = not item.can_clear
            clear_button.tooltip = f"{item.clear_label}：{item.label}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """只轉送本元件的清除請求，不傳播原始按鈕事件。

        Args:
            event: Textual 傳入的按鈕按下事件。

        Returns:
            None。
        """
        button_id = event.button.id or ""
        if not button_id.startswith(_CLEAR_BUTTON_PREFIX):
            return

        key = button_id[len(_CLEAR_BUTTON_PREFIX) :]
        if key not in _CONDITION_KEYS:
            return

        event.stop()
        self.post_message(self.ClearRequested(key))
