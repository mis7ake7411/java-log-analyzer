from __future__ import annotations

import asyncio
from math import ceil

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from log_analyzer.application.analysis_service import AnalysisResult
from log_analyzer.application.event_correlation import (
    CorrelatedOccurrence,
    find_correlated_occurrences,
)

_PAGE_SIZE = 100


class EventCorrelationScreen(ModalScreen[None]):
    """依分析結果保留的 MDC 精確查找跨檔出現位置。"""

    def __init__(
        self,
        result: AnalysisResult,
        *,
        initial_key: str = "traceId",
        initial_value: str = "",
    ) -> None:
        self.result = result
        self.initial_key = initial_key
        self.initial_value = initial_value
        self._matches: list[CorrelatedOccurrence] = []
        self._page_start = 0
        self._applied_query: tuple[str, str] | None = None
        self._query_task: asyncio.Task | None = None
        self._querying = False
        self._valid = True
        super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="mdc-correlation-dialog"), Container(
            id="mdc-correlation-card"
        ):
            yield Static("MDC 關聯追查", id="mdc-correlation-title")
            yield Static(
                "只涵蓋本次分析保留的事件；不回讀來源日誌。",
                id="mdc-correlation-hint",
            )
            with Container(id="mdc-correlation-fields"):
                yield Input(
                    value=self.initial_key,
                    placeholder="MDC key",
                    tooltip="精確、區分大小寫的 MDC 欄位名稱",
                    id="mdc-key",
                )
                yield Input(
                    value=self.initial_value,
                    placeholder="MDC value",
                    tooltip="精確、區分大小寫的 MDC 欄位值",
                    id="mdc-value",
                )
            yield Static(
                "輸入 MDC key 和 value，再按查詢。舊結果沒有 MDC 時不會猜測關聯。",
                id="correlation-status",
            )
            yield Static("", id="correlation-page")
            yield OptionList(id="correlation-options")
            with Container(id="correlation-actions"):
                yield Button("查詢", id="correlation-search")
                yield Button("清除", id="correlation-clear")
                yield Button("上一頁", id="correlation-prev", disabled=True)
                yield Button("下一頁", id="correlation-next", disabled=True)
                yield Button("關閉", id="correlation-close")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"mdc-key", "mdc-value"}:
            event.stop()
            self._start_query()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "correlation-search": self._start_query,
            "correlation-clear": self._clear_query,
            "correlation-prev": lambda: self._move_page(-1),
            "correlation-next": lambda: self._move_page(1),
            "correlation-close": self.dismiss,
        }
        handler = handlers.get(event.button.id)
        if handler is not None:
            handler()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "correlation-options" or not self._valid:
            return
        try:
            page_index = int(event.option.id or "")
        except ValueError:
            return
        result_index = self._page_start + page_index
        if 0 <= result_index < len(self._matches):
            self._open_preview(self._matches[result_index])

    def on_unmount(self) -> None:
        self._valid = False
        if self._query_task is not None and not self._query_task.done():
            self._query_task.cancel()

    def _start_query(self) -> None:
        if not self._valid or self._querying:
            return
        key = self.query_one("#mdc-key", Input).value
        value = self.query_one("#mdc-value", Input).value
        if not key or not value:
            self._set_status("MDC key 和 value 都必須有值，空值不建立關聯。", error=True)
            return

        self._querying = True
        self._set_busy(True)
        self._set_status("正在搜尋本次分析保留的事件…")
        self._query_task = asyncio.create_task(self._run_query(key, value))

    async def _run_query(self, key: str, value: str) -> None:
        task = asyncio.current_task()
        try:
            matches = await find_correlated_occurrences(
                self.result.matched_logs,
                key,
                value,
            )
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, EOFError) as exc:
            if self._is_current_query(task):
                self._report_query_error(str(exc))
        else:
            if self._is_current_query(task):
                self._matches = matches
                self._page_start = 0
                self._applied_query = (key, value)
                self._render_page()
                if matches:
                    self._set_status(
                        f"已查詢 {key}={value}；找到 {len(matches)} 筆保留事件。"
                    )
                else:
                    self._set_status(
                        f"已查詢 {key}={value}；沒有符合的保留事件；"
                        "舊結果可能沒有 MDC，請確認 key/value。"
                    )
        finally:
            if self._is_current_query(task):
                self._querying = False
                self._set_busy(False)

    def _is_current_query(self, task: asyncio.Task | None) -> bool:
        return bool(
            self._valid
            and self.is_mounted
            and task is not None
            and self._query_task is task
        )

    def _set_busy(self, busy: bool) -> None:
        for selector in ("#mdc-key", "#mdc-value", "#correlation-search", "#correlation-clear"):
            self.query_one(selector).disabled = busy
        self.query_one("#correlation-options", OptionList).disabled = busy
        self.query_one("#correlation-prev", Button).disabled = busy or self._page_start <= 0
        self.query_one("#correlation-next", Button).disabled = busy or (
            self._page_start + _PAGE_SIZE >= len(self._matches)
        )

    def _clear_query(self) -> None:
        if not self._valid or self._querying:
            return
        self.query_one("#mdc-key", Input).value = "traceId"
        self.query_one("#mdc-value", Input).value = ""
        self._matches = []
        self._page_start = 0
        self._applied_query = None
        self._render_page()
        self._set_status("輸入 MDC key 和 value，再按查詢。")

    def _move_page(self, direction: int) -> None:
        if not self._valid or self._querying:
            return
        next_start = self._page_start + direction * _PAGE_SIZE
        if 0 <= next_start < len(self._matches):
            self._page_start = next_start
            self._render_page()

    def _render_page(self) -> None:
        end = min(self._page_start + _PAGE_SIZE, len(self._matches))
        page = self._matches[self._page_start : end]
        options = [
            Option(self._result_label(occurrence), id=str(page_index))
            for page_index, occurrence in enumerate(page)
        ]
        option_list = self.query_one("#correlation-options", OptionList)
        option_list.set_options(options)
        option_list.highlighted = 0 if options else None

        page_count = max(1, ceil(len(self._matches) / _PAGE_SIZE))
        page_number = self._page_start // _PAGE_SIZE + 1
        label = (
            f"第 {page_number} 頁 / 共 {page_count} 頁｜第 {self._page_start + 1}-"
            f"{end} 筆 / 共 {len(self._matches)} 筆"
            if self._matches
            else ""
        )
        self.query_one("#correlation-page", Static).update(Text(label))
        self._set_busy(self._querying)

    @staticmethod
    def _result_label(occurrence: CorrelatedOccurrence) -> Text:
        label = Text()
        label.append(
            f"{occurrence.timestamp}  {occurrence.level}  {occurrence.logger}\n",
            style="bold cyan",
        )
        label.append(f"{occurrence.message}\n")
        label.append(
            f"{occurrence.filename}:{occurrence.line_number}  Thread: {occurrence.thread}"
        )
        return label

    def _set_status(self, message: str, *, error: bool = False) -> None:
        status = Text(message)
        if error:
            status.stylize("bold red")
        self.query_one("#correlation-status", Static).update(status)

    def _report_query_error(self, message: str) -> None:
        if self._applied_query is None:
            self._set_status(f"查詢失敗：{message}", error=True)
            return
        key, value = self._applied_query
        self._set_status(
            f"查詢失敗：{message}；保留上次查詢 {key}={value} 的 "
            f"{len(self._matches)} 筆結果。",
            error=True,
        )

    def _open_preview(self, occurrence: CorrelatedOccurrence) -> None:
        from log_analyzer.presentation.tui.screens.event_browser import (
            EventBrowserScreen,
        )

        self.app.push_screen(
            EventBrowserScreen(
                self.result,
                initial_group_index=occurrence.group_index,
                initial_occurrence_index=occurrence.occurrence_index,
            )
        )
