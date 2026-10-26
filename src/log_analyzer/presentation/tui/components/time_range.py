"""時間範圍快速選單與明確起訖欄位。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Input, Label, Select, Static

from log_analyzer.presentation.tui.components.inputs import ShortcutInput
from log_analyzer.presentation.tui.form_values import (
    get_default_end_date_text,
    get_default_end_time_text,
    get_default_start_date_text,
    get_default_start_time_text,
    parse_datetime_range_inputs,
)

_FIELD_IDS = ("start_date", "start_time", "end_date", "end_time")
_PRESET_NONE = "none"
_PRESET_TODAY = "today"
_PRESET_LAST_HOUR = "last_hour"
_PRESET_CUSTOM = "custom"


def _local_now() -> datetime:
    """回傳含系統本地時區的目前時間。

    Returns:
        datetime: 已附系統本地時區的目前時間。
    """
    return datetime.now().astimezone()


class TimeRangeSettings(Vertical):
    """顯示常用時間範圍選單，並保留既有明確日期與時間欄位。

    Args:
        now_provider: 提供本地目前時間的函式，供快速範圍測試固定時刻。
    """

    def __init__(
        self,
        *,
        now_provider: Callable[[], datetime] | None = None,
        **kwargs: object,
    ) -> None:
        """初始化時間選單與其本地時間來源。

        Args:
            now_provider: 回傳含本地時區時間的函式；省略時使用系統時間。
            **kwargs: 傳給 Textual `Vertical` 的標準 widget 參數。

        Returns:
            None: 初始化時間選單與時間來源。
        """
        extra_classes = kwargs.pop("classes", ())
        if isinstance(extra_classes, str):
            extra_class_names = extra_classes.split()
        else:
            extra_class_names = list(extra_classes) if extra_classes else []
        kwargs["classes"] = " ".join(
            dict.fromkeys(("time-section", "time-range-settings", *extra_class_names))
        )
        super().__init__(**kwargs)
        self._now_provider = now_provider if now_provider is not None else _local_now
        self._pending_input_snapshot: tuple[str, str, str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Label("時間區間")
        with Container(id="time-range-controls", classes="time-stack"):
            yield Select(
                [
                    ("不限時間", _PRESET_NONE),
                    ("今天", _PRESET_TODAY),
                    ("最近一小時", _PRESET_LAST_HOUR),
                    ("自訂範圍", _PRESET_CUSTOM),
                ],
                value=_PRESET_NONE,
                allow_blank=False,
                id="time-preset",
                tooltip="快捷範圍依本機時間於選取時計算；分析使用下方顯示的起訖值。",
            )
            yield Static("時間範圍：不限", id="time-range-summary", markup=False)
            with Container(id="time-range-custom", classes="time-range-custom"):
                with Container(id="start-time-row", classes="time-row"):
                    with Container(id="start-date-group", classes="time-group"):
                        yield Label("開始日期")
                        yield ShortcutInput(
                            value=get_default_start_date_text(),
                            placeholder="YYYY-MM-DD",
                            id="start_date",
                        )
                    with Container(id="start-time-group", classes="time-group"):
                        yield Label("開始時間")
                        yield ShortcutInput(
                            value=get_default_start_time_text(),
                            placeholder="HH:MM[:SS]",
                            id="start_time",
                        )
                with Container(id="end-time-row", classes="time-row"):
                    with Container(id="end-date-group", classes="time-group"):
                        yield Label("結束日期")
                        yield ShortcutInput(
                            value=get_default_end_date_text(),
                            placeholder="YYYY-MM-DD",
                            id="end_date",
                        )
                    with Container(id="end-time-group", classes="time-group"):
                        yield Label("結束時間")
                        yield ShortcutInput(
                            value=get_default_end_time_text(),
                            placeholder="HH:MM[:SS]",
                            id="end_time",
                        )

    def on_mount(self) -> None:
        """依掛載時既有欄位值顯示自訂範圍或不限時間。"""
        self._sync_from_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        """套用選單範圍，阻止選單事件觸發外層表單分析。

        Args:
            event: 時間範圍 Select 的變更事件。

        Returns:
            None: 選單事件在此元件處理；寫入欄位產生的事件仍會冒泡。
        """
        if event.select.id != "time-preset":
            return
        event.stop()
        if event.value == _PRESET_CUSTOM:
            self._pending_input_snapshot = None
            self._show_custom_fields(True)
            self._refresh_summary(self._read_values())
            return

        now = self._now_provider()
        if now.tzinfo is None:
            now = now.astimezone()
        now = now.replace(microsecond=0)
        if event.value == _PRESET_TODAY:
            date_text = now.date().isoformat()
            values = (date_text, "", date_text, "")
        elif event.value == _PRESET_LAST_HOUR:
            start = now - timedelta(hours=1)
            values = (
                start.date().isoformat(),
                start.strftime("%H:%M:%S"),
                now.date().isoformat(),
                now.strftime("%H:%M:%S"),
            )
        else:
            values = ("", "", "", "")

        self._pending_input_snapshot = values
        self._write_values(values)
        self._show_custom_fields(False)
        self._refresh_summary(values)

    def on_input_changed(self, event: Input.Changed) -> None:
        """把欄位同步成自訂或不限狀態，並保留 App 的表單追蹤事件。

        Args:
            event: 四個時間 Input 之一的變更事件。

        Returns:
            None: 完整匹配的 pending snapshot 只避免快捷值被誤判為自訂；
                事件仍冒泡，讓 App 更新條件總覽、配方差異與結果提示。
        """
        if event.input.id not in _FIELD_IDS:
            return

        values = self._read_values()
        if (
            self._pending_input_snapshot == values
            and event.value == values[_FIELD_IDS.index(event.input.id)]
        ):
            return

        self._pending_input_snapshot = None
        if any(value.strip() for value in values):
            self.query_one("#time-preset", Select).value = _PRESET_CUSTOM
            self._show_custom_fields(True)
            self._refresh_summary(values)
        else:
            self.query_one("#time-preset", Select).value = _PRESET_NONE
            self._show_custom_fields(False)
            self._refresh_summary(values)

    def _sync_from_fields(self) -> None:
        """依掛載時四欄的明確值初始化選單，支援近期狀態與配方還原。"""
        values = self._read_values()
        if any(value.strip() for value in values):
            self.query_one("#time-preset", Select).value = _PRESET_CUSTOM
            self._show_custom_fields(True)
        else:
            self._show_custom_fields(False)
        self._refresh_summary(values)

    def _read_values(self) -> tuple[str, str, str, str]:
        """讀取明確的起訖值。

        Returns:
            tuple[str, str, str, str]: 依序為開始日期、開始時間、結束日期、
                結束時間。
        """
        return (
            self.query_one("#start_date", Input).value,
            self.query_one("#start_time", Input).value,
            self.query_one("#end_date", Input).value,
            self.query_one("#end_time", Input).value,
        )

    def _write_values(self, values: tuple[str, str, str, str]) -> None:
        """更新四個欄位，供選單選項寫入固定時間值。

        Args:
            values: 依序為開始日期、開始時間、結束日期、結束時間。

        Returns:
            None: 欄位會保留明確值供既有分析、近期狀態與配方流程使用。
        """
        for field_id, value in zip(_FIELD_IDS, values):
            self.query_one(f"#{field_id}", Input).value = value

    def _show_custom_fields(self, visible: bool) -> None:
        """展開或收合自訂範圍欄位，但不卸載其 DOM。

        Args:
            visible: 是否顯示四個日期與時間欄位。

        Returns:
            None: 收合時欄位仍 mounted，保留既有 DOM ID 與查詢方式。
        """
        self.query_one("#time-range-custom", Container).display = visible

    def _refresh_summary(self, values: tuple[str, str, str, str]) -> None:
        """顯示實際邊界；無效的自訂值則標示尚未完成並保留原輸入。

        Args:
            values: 依序為開始日期、開始時間、結束日期、結束時間。

        Returns:
            None: 摘要只呈現表單值，不改寫 parser 或分析請求。
        """
        summary = self.query_one("#time-range-summary", Static)
        if not any(value.strip() for value in values):
            summary.update("時間範圍：不限")
            return

        try:
            start, end = parse_datetime_range_inputs(*values)
            start_text = self._format_endpoint(start, "不限起點")
            end_text = self._format_endpoint(end, "不限終點")
        except ValueError:
            start_text = self._raw_endpoint(values[0], values[1], "不限起點")
            end_text = self._raw_endpoint(values[2], values[3], "不限終點")
            summary.update(f"自訂範圍尚未完成：{start_text} ～ {end_text}")
            return
        summary.update(f"實際範圍：{start_text} ～ {end_text}")

    @staticmethod
    def _format_endpoint(value: datetime | None, empty_label: str) -> str:
        """格式化解析後的時間邊界，保留秒數並省略時區 offset。

        Args:
            value: parser 解析後的本地時間；None 表示該端未限制。
            empty_label: 該端未限制時要顯示的文字。

        Returns:
            str: 可供摘要顯示的日期時間或未限制文字。
        """
        if value is None:
            return empty_label
        return value.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")

    @staticmethod
    def _raw_endpoint(date_text: str, time_text: str, empty_label: str) -> str:
        """日期或時間尚無法解析時，顯示使用者目前輸入的內容。

        Args:
            date_text: 該端的日期欄位原文。
            time_text: 該端的時間欄位原文。
            empty_label: 日期與時間皆空白時要顯示的文字。

        Returns:
            str: 日期與時間合併後的原文，或未限制文字。
        """
        raw_value = " ".join(part.strip() for part in (date_text, time_text) if part.strip())
        return raw_value or empty_label
