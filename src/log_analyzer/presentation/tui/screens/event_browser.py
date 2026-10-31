from __future__ import annotations
import asyncio
from dataclasses import dataclass
from functools import partial
from math import ceil
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option
from log_analyzer.application.analysis.service import AnalysisResult
from log_analyzer.application.events.query import EventQueryFilters, find_group_indices
from log_analyzer.infrastructure.sources.context_index import SourceContextReader
from log_analyzer.application.sources.preview import SourcePreview, read_source_context
from log_analyzer.domain.log_types import LogEntry, OccurrenceCoordinate
from log_analyzer.presentation.tui.event_browser_text import (
    STACKTRACE_PREVIEW_LINES,
    highlight_literal,
    representative_message,
    retained_stacktrace,
    visible_stacktrace,
)


@dataclass(frozen=True, slots=True)
class _BrowserQueryDraft:
    query: str
    sort_by: str
    filters: EventQueryFilters

    @property
    def requires_index(self) -> bool:
        return bool(
            self.query
            or self.sort_by != "original"
            or self.filters.level is not None
            or self.filters.group_index is not None
            or self.filters.logger_filter
            or self.filters.thread_filter
            or self.filters.source_filename_filter
        )


@dataclass(frozen=True, slots=True)
class _AppliedBrowserQuery:
    group_indices: list[int] | None
    query_sort: str
    query: str | None
    group_start: int
    group_highlight: int
    filters: EventQueryFilters


class EventBrowserScreen(ModalScreen[None]):
    """Browse matched groups and their verified source coordinates."""

    def __init__(
        self,
        result: AnalysisResult,
        page_size: int = 100,
        *,
        initial_level: str | None = None,
        initial_group_index: int | None = None,
        initial_occurrence_index: int | None = None,
    ) -> None:
        """開啟原結果的瀏覽範圍，篩選只建立索引、不修改原群組。

        Args:
            result: 仍有效的分析結果。
            page_size: 每頁群組數，上限為一百。
            initial_level: 摘要點選的精確 Level；None 表示不限制。
            initial_group_index: 摘要點選的原始群組索引；0 也是有效索引。
        """
        self.result = result
        self.page_size = min(max(1, int(page_size)), 100)
        self._view = "groups"
        self._valid = True
        self._group_start = 0
        self._group_indices: list[int] | None = None
        self._group_highlight = 0
        self._occurrence_highlight = 0
        self._query_sort = "original"
        self._active_browser_query: str | None = None
        self._scope_level = initial_level
        self._scope_group_index = initial_group_index
        self._initial_occurrence_index = initial_occurrence_index
        self._active_filters = EventQueryFilters(
            level=initial_level,
            group_index=initial_group_index,
        )
        self._advanced_open = False
        self._query_task: asyncio.Task | None = None
        self._querying = False
        self._group_page: list[LogEntry] = []
        self._selected_group: LogEntry | None = None
        self._occurrences: list[OccurrenceCoordinate] = []
        self._occurrence_order: list[int] = []
        self._occurrence_start = 0
        self._source_context_reader = SourceContextReader()
        self._current_preview: SourcePreview | None = None
        self._current_occurrence: OccurrenceCoordinate | None = None
        self._stacktrace_expanded = False
        self._copy_path_line: str | None = None
        super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="event-browser"), Container(id="event-browser-card"):
            yield Static("事件瀏覽", id="browser-title")
            yield Static("", id="browser-page")
            yield Static("", id="browser-sort-hint")
            with Container(id="browser-controls"):
                with Container(id="browser-main-controls"):
                    yield Input(
                        placeholder="搜尋訊息、Logger 或 Thread", id="browser-query"
                    )
                    with Container(id="browser-query-toolbar"):
                        yield Select(
                            [
                                ("分析原排序", "original"),
                                ("出現次數最多", "count"),
                                ("最近出現", "recent"),
                            ],
                            value="original",
                            allow_blank=False,
                            id="browser-order",
                        )
                        yield Button("套用", id="browser-apply")
                        yield Button(
                            "清除",
                            id="browser-clear",
                            tooltip="清除搜尋、進階條件、排序與摘要範圍",
                        )
                        yield Button("進階篩選", id="browser-advanced-toggle")
                with VerticalScroll(id="browser-advanced"):
                    yield Select(
                        [
                            ("預設（訊息、Logger、Thread）", "default"),
                            ("訊息", "message"),
                            ("堆疊追蹤", "stacktrace"),
                            ("Logger", "logger"),
                            ("Thread", "thread"),
                        ],
                        value="default",
                        allow_blank=False,
                        id="browser-query-scope",
                    )
                    yield Input(
                        placeholder="Logger 子字串",
                        tooltip="忽略大小寫的字面子字串條件",
                        id="browser-logger-filter",
                    )
                    yield Input(
                        placeholder="Thread 子字串",
                        tooltip="逐次出現位置的忽略大小寫字面子字串條件",
                        id="browser-thread-filter",
                    )
                    yield Input(
                        placeholder="來源檔名子字串",
                        tooltip="逐次來源檔名的忽略大小寫字面子字串條件",
                        id="browser-source-filter",
                    )
            yield Static("", id="browser-query-status")
            yield OptionList(id="event-options")
            with VerticalScroll(id="browser-preview-content"):
                yield Static("", id="source-warning")
                yield Static("", id="source-encoding-notice")
                with Container(id="event-browser-preview-toolbar"):
                    yield Button("複製代表訊息", id="copy-retained-message")
                    yield Button("複製完整堆疊", id="copy-full-stacktrace")
                    yield Button("展開堆疊", id="stacktrace-toggle")
                yield Static("", id="source-path")
                yield Static("", id="retained-details")
                yield Static("", id="source-context")
                yield Static("", id="copy-feedback")
            with Container(id="event-browser-actions"):
                yield Button("上一頁", id="browser-previous")
                yield Button("下一頁", id="browser-next")
                yield Button("返回", id="browser-back")
                yield Button("複製路徑:行號", id="copy-coordinate", disabled=True)
                yield Button("MDC 關聯追查", id="browser-correlation")
                yield Button("關閉", id="close-event-browser")

    def on_mount(self) -> None:
        self._update_browser_layout()
        if self._scope_level is not None or self._scope_group_index is not None:
            self._start_query()
        else:
            self._render_group_page()
        self.query_one("#event-options", OptionList).focus()

    def on_unmount(self) -> None:
        self.invalidate()

    def on_resize(self) -> None:
        if self.is_mounted:
            self._update_browser_layout()

    def _update_browser_layout(self) -> None:
        controls = self.query_one("#browser-controls")
        controls.set_class(self.size.width >= 100, "wide")
        controls.set_class(self._advanced_open, "advanced-open")
        self.query_one("#browser-main-controls").set_class(
            self.size.width >= 100, "wide"
        )
        advanced = self.query_one("#browser-advanced", VerticalScroll)
        advanced.display = self._advanced_open
        advanced.set_class(self.size.width >= 100, "wide")
        self.query_one("#browser-advanced-toggle", Button).label = (
            "收合篩選" if self._advanced_open else "進階篩選"
        )
        self.query_one("#event-browser-preview-toolbar").set_class(
            self.size.width >= 100, "wide"
        )
        self.query_one("#event-browser-card").set_class(self.size.height < 30, "short")
        self.query_one("#browser-sort-hint").display = (
            self._view != "preview" and self.size.height >= 30
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {
            "browser-query",
            "browser-logger-filter",
            "browser-thread-filter",
            "browser-source-filter",
        }:
            event.stop()
            self._start_query()

    def _start_query(self) -> None:
        if self._valid and not self._querying and self._view == "groups":
            self._querying = True
            self._query_task = asyncio.create_task(self._apply_group_query())

    async def _apply_group_query(self) -> None:
        controls = self._query_controls()
        status = self.query_one("#browser-query-status", Static)
        status.update(Text("正在搜尋已分析結果…"))
        for widget in controls:
            widget.disabled = True
        self.query_one("#event-options").disabled = True
        self.query_one("#browser-previous").disabled = True
        self.query_one("#browser-next").disabled = True
        previous = self._capture_applied_query()
        try:
            draft = self._query_draft()
            indices = await self._run_group_query(draft)
            if not self._valid:
                return
            self._commit_group_query(draft, indices)
            self._render_group_page()
            self._open_initial_occurrence()
            self._update_query_status()
        except (OSError, ValueError, EOFError) as exc:
            self._restore_applied_query(previous)
            self._update_query_status(error=str(exc))
        finally:
            self._finish_group_query(controls)

    def _query_controls(self) -> list:
        return [
            self.query_one(selector, widget_type)
            for selector, widget_type in (
                ("#browser-query", Input),
                ("#browser-order", Select),
                ("#browser-query-scope", Select),
                ("#browser-logger-filter", Input),
                ("#browser-thread-filter", Input),
                ("#browser-source-filter", Input),
                ("#browser-advanced-toggle", Button),
                ("#browser-apply", Button),
                ("#browser-clear", Button),
            )
        ]

    def _query_draft(self) -> _BrowserQueryDraft:
        return _BrowserQueryDraft(
            query=self.query_one("#browser-query", Input).value.strip(),
            sort_by=str(self.query_one("#browser-order", Select).value),
            filters=EventQueryFilters(
                query_scope=str(
                    self.query_one("#browser-query-scope", Select).value or "default"
                ),
                logger_filter=self.query_one("#browser-logger-filter", Input).value.strip(),
                thread_filter=self.query_one("#browser-thread-filter", Input).value.strip(),
                source_filename_filter=self.query_one("#browser-source-filter", Input).value.strip(),
                level=self._scope_level,
                group_index=self._scope_group_index,
            ),
        )

    async def _run_group_query(self, draft: _BrowserQueryDraft) -> list[int] | None:
        if not draft.requires_index:
            return None
        return await find_group_indices(
            self.result.matched_logs,
            draft.query,
            draft.sort_by,
            draft.filters,
        )

    def _capture_applied_query(self) -> _AppliedBrowserQuery:
        return _AppliedBrowserQuery(
            group_indices=self._group_indices,
            query_sort=self._query_sort,
            query=self._active_browser_query,
            group_start=self._group_start,
            group_highlight=self._group_highlight,
            filters=self._active_filters,
        )

    def _commit_group_query(
        self, draft: _BrowserQueryDraft, indices: list[int] | None
    ) -> None:
        self._group_indices = indices
        self._query_sort = draft.sort_by
        self._active_browser_query = draft.query or None
        self._active_filters = draft.filters
        self._group_start = self._group_highlight = 0

    def _restore_applied_query(self, state: _AppliedBrowserQuery) -> None:
        self._group_indices = state.group_indices
        self._query_sort = state.query_sort
        self._active_browser_query = state.query
        self._group_start = state.group_start
        self._group_highlight = state.group_highlight
        self._active_filters = state.filters
        self._scope_level = state.filters.level
        self._scope_group_index = state.filters.group_index

    def _finish_group_query(self, controls: list) -> None:
        self._querying = False
        if self._valid and self.is_mounted:
            for widget in controls:
                widget.disabled = False
            self.query_one("#event-options").disabled = False
            self.query_one("#browser-previous").disabled = self._group_start <= 0
            self.query_one("#browser-next").disabled = (
                self._group_start + self.page_size >= self._group_length()
            )
            self.query_one("#event-options").focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self._valid or event.option_list.id != "event-options":
            return
        try:
            page_index = int(event.option.id or "")
        except ValueError:
            return

        if self._view == "groups":
            if page_index < 0 or page_index >= len(self._group_page):
                return
            self._selected_group = self._group_page[page_index]
            self._group_highlight = page_index
            occurrences = self._selected_group.get("occurrences")
            self._occurrences = occurrences if isinstance(occurrences, list) else []
            self._occurrence_order = sorted(
                range(len(self._occurrences)),
                key=self._occurrence_sort_key,
            )
            self._occurrence_start = 0
            self._occurrence_highlight = 0
            self._render_occurrence_page()
        elif self._view == "occurrences":
            absolute_index = self._occurrence_start + page_index
            if absolute_index < 0 or absolute_index >= len(self._occurrence_order):
                return
            occurrence_index = self._occurrence_order[absolute_index]
            self._occurrence_highlight = page_index
            self._render_preview(self._occurrences[occurrence_index])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "close-event-browser": self.dismiss,
            "browser-apply": self._start_query,
            "browser-clear": self._clear_filters,
            "browser-advanced-toggle": self._toggle_advanced_filters,
            "browser-previous": partial(self._move_page, -1),
            "browser-next": partial(self._move_page, 1),
            "browser-back": self._go_back,
            "copy-coordinate": self._copy_coordinate,
            "copy-retained-message": self._copy_retained_message,
            "copy-full-stacktrace": self._copy_full_stacktrace,
            "stacktrace-toggle": self._toggle_stacktrace,
            "browser-correlation": self._open_correlation,
        }
        handler = handlers.get(event.button.id)
        if handler is not None:
            handler()

    def _clear_filters(self) -> None:
        self._scope_level = None
        self._scope_group_index = None
        for selector, widget_type, value in (
            ("#browser-query", Input, ""),
            ("#browser-order", Select, "original"),
            ("#browser-query-scope", Select, "default"),
            ("#browser-logger-filter", Input, ""),
            ("#browser-thread-filter", Input, ""),
            ("#browser-source-filter", Input, ""),
        ):
            self.query_one(selector, widget_type).value = value
        self._advanced_open = False
        self._update_browser_layout()
        self._start_query()

    def _toggle_advanced_filters(self) -> None:
        self._advanced_open = not self._advanced_open
        self._update_browser_layout()

    def _go_back(self) -> None:
        if self._view == "preview":
            self._render_occurrence_page()
        elif self._view == "occurrences":
            self._render_group_page()
        self.query_one("#event-options").focus()

    def _toggle_stacktrace(self) -> None:
        self._stacktrace_expanded = not self._stacktrace_expanded
        self._update_retained_details()
        self.query_one("#stacktrace-toggle", Button).focus()

    def _open_correlation(self) -> None:
        from log_analyzer.presentation.tui.screens.event_correlation import (
            EventCorrelationScreen,
        )

        initial_key = "traceId"
        initial_value = ""
        occurrence = self._selected_occurrence_for_correlation()
        mdc = occurrence.get("mdc") if occurrence is not None else None
        if isinstance(mdc, dict):
            preferred_keys = ["traceId", "requestId", *mdc.keys()]
            for key in dict.fromkeys(preferred_keys):
                value = mdc.get(key)
                if isinstance(key, str) and isinstance(value, str) and value:
                    initial_key, initial_value = key, value
                    break

        self.app.push_screen(
            EventCorrelationScreen(
                self.result,
                initial_key=initial_key,
                initial_value=initial_value,
            )
        )

    def _selected_occurrence_for_correlation(self) -> OccurrenceCoordinate | None:
        if self._view == "preview":
            return self._current_occurrence
        if self._view != "occurrences":
            return None

        highlighted = self.query_one("#event-options", OptionList).highlighted
        if highlighted is None:
            return None
        absolute_index = self._occurrence_start + highlighted
        if absolute_index < 0 or absolute_index >= len(self._occurrence_order):
            return None
        occurrence_index = self._occurrence_order[absolute_index]
        if occurrence_index < 0 or occurrence_index >= len(self._occurrences):
            return None
        return self._occurrences[occurrence_index]

    def _open_initial_occurrence(self) -> None:
        occurrence_index = self._initial_occurrence_index
        group_index = self._scope_group_index
        self._initial_occurrence_index = None
        if occurrence_index is None or group_index is None or occurrence_index < 0:
            return

        try:
            group = self.result.matched_logs[group_index]
        except (IndexError, OSError, ValueError):
            return
        occurrences = group.get("occurrences")
        if not isinstance(occurrences, list) or occurrence_index >= len(occurrences):
            return

        self._selected_group = group
        self._occurrences = occurrences
        self._occurrence_order = sorted(
            range(len(occurrences)),
            key=self._occurrence_sort_key,
        )
        try:
            result_position = self._occurrence_order.index(occurrence_index)
        except ValueError:
            return
        self._occurrence_start = result_position // self.page_size * self.page_size
        self._occurrence_highlight = result_position % self.page_size
        self._render_occurrence_page()
        self._render_preview(occurrences[occurrence_index])

    def invalidate(self) -> None:
        """Prevent cached coordinates from being used after their result is released."""
        self._valid = False
        if self._query_task is not None and not self._query_task.done():
            self._query_task.cancel()
        self._group_indices = None
        self._group_page.clear()
        self._selected_group = None
        self._occurrences = []
        self._occurrence_order.clear()
        self._source_context_reader.clear()
        self._current_preview = None
        self._current_occurrence = None
        self._copy_path_line = None

    def _move_page(self, direction: int) -> None:
        if not self._valid:
            return
        if self._view == "groups":
            length = self._group_length()
            next_start = self._group_start + direction * self.page_size
            if 0 <= next_start < length:
                self._group_start = next_start
                self._group_highlight = 0
                self._render_group_page()
        elif self._view == "occurrences":
            next_start = self._occurrence_start + direction * self.page_size
            if 0 <= next_start < len(self._occurrence_order):
                self._occurrence_start = next_start
                self._occurrence_highlight = 0
                self._render_occurrence_page()

    def _render_group_page(self) -> None:
        if not self._valid:
            return
        self._view = "groups"
        self._selected_group = None
        self._occurrences = []
        self._occurrence_order.clear()
        self._occurrence_start = 0
        store = self.result.matched_logs
        length = self._group_length()
        iter_page = getattr(store, "iter_page", None)
        if self._group_indices is not None:
            self._group_page = [
                store[index]
                for index in self._group_indices[
                    self._group_start : self._group_start + self.page_size
                ]
            ]
        elif callable(iter_page):
            self._group_page = list(iter_page(self._group_start, self.page_size))
        else:
            end = min(self._group_start + self.page_size, length)
            self._group_page = [store[index] for index in range(self._group_start, end)]

        keyword, ignore_case = self._highlight_query()
        options = [
            Option(
                highlight_literal(self._group_label(group), keyword, ignore_case),
                id=str(page_index),
            )
            for page_index, group in enumerate(self._group_page)
        ]
        self._set_list_state(
            title="符合事件群組",
            options=options,
            start=self._group_start,
            length=length,
        )
        if options:
            self.query_one("#event-options", OptionList).highlighted = min(
                self._group_highlight, len(options) - 1
            )

    def _group_length(self) -> int:
        return (
            len(self.result.matched_logs)
            if self._group_indices is None
            else len(self._group_indices)
        )

    def _has_applied_group_filter(self) -> bool:
        filters = self._active_filters
        return bool(
            self._active_browser_query
            or filters.logger_filter
            or filters.thread_filter
            or filters.source_filename_filter
            or filters.level is not None
            or filters.group_index is not None
        )

    def _update_query_status(self, *, error: str | None = None) -> None:
        text = Text()
        if error:
            text.append(f"無法搜尋：{error}\n", style="bold red")
        parts = self._applied_query_parts()
        if parts:
            text.append("條件：", style="bold")
            text.append("；".join(parts))
            text.append("\n")

        if self._view == "occurrences":
            if self._has_applied_group_filter():
                text.append("群組篩選；出現位置列出整個群組", style="bold yellow")
        elif self._view == "groups":
            self._append_group_result_count(text)
        else:
            text.plain = ""
        self.query_one("#browser-query-status", Static).update(text)

    def _applied_query_parts(self) -> list[str]:
        filters = self._active_filters
        parts: list[str] = []
        if self._active_browser_query:
            scope_labels = {
                "default": "預設欄位",
                "message": "訊息",
                "stacktrace": "堆疊追蹤",
                "logger": "Logger",
                "thread": "Thread",
            }
            parts.append(
                f"{scope_labels[filters.query_scope]}：{self._active_browser_query}"
            )
        if filters.logger_filter:
            parts.append(f"Logger：{filters.logger_filter}")
        if filters.thread_filter:
            parts.append(f"Thread：{filters.thread_filter}")
        if filters.source_filename_filter:
            parts.append(f"來源檔名：{filters.source_filename_filter}")
        if filters.level is not None:
            parts.append(f"Level：{filters.level}")
        if filters.group_index is not None:
            parts.append(f"事件群組：#{filters.group_index + 1}")
        return parts

    def _append_group_result_count(self, text: Text) -> None:
        if self._group_indices is not None:
            count = len(self._group_indices)
            text.append(
                f"瀏覽 {count}/{len(self.result.matched_logs)} 群組；分析結果與匯出內容未變更。"
            )
        elif self._has_applied_group_filter():
            text.append(
                f"瀏覽 {self._group_length()}/{len(self.result.matched_logs)} 群組；分析結果與匯出內容未變更。"
            )

    def _render_occurrence_page(self) -> None:
        if not self._valid or self._selected_group is None:
            return
        self._view = "occurrences"
        self._current_preview = None
        self._current_occurrence = None
        self._copy_path_line = None
        length = len(self._occurrence_order)
        end = min(self._occurrence_start + self.page_size, length)
        page = [
            self._occurrences[self._occurrence_order[index]]
            for index in range(self._occurrence_start, end)
        ]
        options = [
            Option(self._occurrence_label(occurrence), id=str(page_index))
            for page_index, occurrence in enumerate(page)
        ]
        self._set_list_state(
            title="事件出現位置",
            options=options,
            start=self._occurrence_start,
            length=length,
        )
        if options:
            self.query_one("#event-options", OptionList).highlighted = min(
                self._occurrence_highlight, len(options) - 1
            )

    def _render_preview(self, occurrence: OccurrenceCoordinate) -> None:
        if not self._valid:
            return
        filename = occurrence.get("filename", "")
        source_states = getattr(self.result, "source_states", {})
        snapshot = source_states.get(filename) if isinstance(filename, str) else None
        preview = read_source_context(
            self.result.input_path,
            occurrence,
            snapshot,
            reader=self._source_context_reader,
        )
        self._view = "preview"
        self.query_one("#browser-controls").display = False
        self.query_one("#browser-query-status").display = False
        self._current_preview = preview
        self._current_occurrence = occurrence
        self._stacktrace_expanded = False
        self._copy_path_line = (
            preview.path_line if preview.warning is None and preview.lines else None
        )

        self.query_one("#browser-title", Static).update("來源內容預覽")
        self.query_one("#browser-page", Static).update("")
        self.query_one("#browser-page", Static).display = False
        self.query_one("#browser-sort-hint", Static).display = False
        self.query_one("#event-options", OptionList).display = False
        self.query_one("#browser-preview-content", VerticalScroll).display = True
        self.query_one("#browser-previous", Button).display = False
        self.query_one("#browser-next", Button).display = False
        back_button = self.query_one("#browser-back", Button)
        back_button.display = True
        back_button.label = "返回事件出現位置"
        warning_widget = self.query_one("#source-warning", Static)
        warning_widget.display = bool(preview.warning)
        warning_widget.update(preview.warning or "")
        encoding_notice_widget = self.query_one("#source-encoding-notice", Static)
        encoding_notice_widget.display = bool(preview.encoding_notice)
        encoding_notice_widget.update(preview.encoding_notice or "")
        self.query_one("#source-path", Static).display = True
        self.query_one("#source-path", Static).update(
            Text(preview.path_line or "", no_wrap=False)
        )
        self._update_retained_details()
        keyword, ignore_case = self._highlight_query()
        context_widget = self.query_one("#source-context", Static)
        has_verified_context = preview.warning is None and bool(preview.lines)
        context_widget.display = has_verified_context
        context_widget.update(
            highlight_literal(self._context_text(preview), keyword, ignore_case)
            if has_verified_context
            else Text()
        )
        self.query_one("#copy-feedback", Static).display = True
        self.query_one("#copy-feedback", Static).update("")
        copy_button = self.query_one("#copy-coordinate", Button)
        copy_button.display = True
        copy_button.disabled = self._copy_path_line is None
        self.query_one("#browser-preview-content", VerticalScroll).scroll_home(
            animate=False
        )

    def _update_retained_details(self) -> None:
        if self._selected_group is None or self._current_occurrence is None:
            return

        group = self._selected_group
        stacktrace = retained_stacktrace(group)
        visible_trace, has_toggle = visible_stacktrace(
            stacktrace,
            expanded=self._stacktrace_expanded,
        )
        details = self._retained_details_text(
            group,
            self._current_occurrence,
            stacktrace_preview=visible_trace,
        )
        keyword, ignore_case = self._highlight_query()
        retained_details = self.query_one("#retained-details", Static)
        retained_details.display = True
        retained_details.update(highlight_literal(details, keyword, ignore_case))

        toolbar = self.query_one("#event-browser-preview-toolbar")
        toolbar.display = True
        self.query_one("#copy-retained-message", Button).disabled = not bool(
            representative_message(group)
        )
        self.query_one("#copy-full-stacktrace", Button).disabled = not bool(
            stacktrace.strip()
        )
        toggle = self.query_one("#stacktrace-toggle", Button)
        toggle.display = has_toggle
        if has_toggle:
            if self._stacktrace_expanded:
                toggle.label = "收合堆疊"
            else:
                remaining = max(
                    0,
                    len(stacktrace.splitlines()) - STACKTRACE_PREVIEW_LINES,
                )
                toggle.label = f"展開堆疊（另有 {remaining} 行）"

    def _highlight_query(self) -> tuple[str | None, bool]:
        if self._active_browser_query:
            # find_group_indices uses casefold for browser queries.
            return self._active_browser_query, True

        analysis_request = getattr(self.result, "analysis_request", None)
        if analysis_request is not None:
            query = getattr(analysis_request, "keyword", None)
            ignore_case = bool(
                getattr(
                    analysis_request,
                    "ignore_case",
                    getattr(self.result, "ignore_case", False),
                )
            )
            return (
                query if isinstance(query, str) and query.strip() else None,
                ignore_case,
            )

        query = getattr(self.result, "keyword", None)
        if not isinstance(query, str) or not query.strip() or query.strip() == "未設定":
            query = None
        return query, bool(getattr(self.result, "ignore_case", False))

    def _set_list_state(
        self, title: str, options: list[Option], start: int, length: int
    ) -> None:
        self.query_one("#browser-controls").display = self._view == "groups"
        status = self.query_one("#browser-query-status", Static)
        status.display = self._view == "groups" or (
            self._view == "occurrences" and self._has_applied_group_filter()
        )
        self._update_query_status()
        self.query_one("#browser-title", Static).update(title)
        first = start + 1 if length else 0
        last = min(start + self.page_size, length)
        page_count = max(1, ceil(length / self.page_size))
        page_number = start // self.page_size + 1
        page_label = (
            f"第 {page_number} 頁 / 共 {page_count} 頁｜第 {first}-{last} 筆 / 共 {length} 筆"
            if length
            else "沒有符合的項目。"
        )
        self.query_one("#browser-page", Static).update(page_label)
        self.query_one("#browser-page", Static).display = True
        sort_hint = self.query_one("#browser-sort-hint", Static)
        sort_hint.display = self.size.height >= 30
        sort_hint.update(self._sort_hint())
        event_options = self.query_one("#event-options", OptionList)
        event_options.set_options(options)
        event_options.highlighted = 0 if options else None
        event_options.display = True
        self.query_one("#browser-preview-content", VerticalScroll).display = False
        self.query_one("#browser-previous", Button).display = True
        self.query_one("#browser-next", Button).display = True
        self.query_one("#browser-previous", Button).disabled = start <= 0
        self.query_one("#browser-next", Button).disabled = (
            start + self.page_size >= length
        )
        back_button = self.query_one("#browser-back", Button)
        back_button.display = self._view != "groups"
        back_button.label = "返回事件群組" if self._view == "occurrences" else "返回"
        self.query_one("#source-warning", Static).display = False
        self.query_one("#source-warning", Static).update("")
        self.query_one("#source-encoding-notice", Static).display = False
        self.query_one("#source-encoding-notice", Static).update("")
        self.query_one("#source-path", Static).display = False
        self.query_one("#source-path", Static).update("")
        self.query_one("#retained-details", Static).display = False
        self.query_one("#retained-details", Static).update("")
        self.query_one("#source-context", Static).display = False
        self.query_one("#source-context", Static).update("")
        self.query_one("#copy-feedback", Static).display = False
        self.query_one("#copy-feedback", Static).update("")
        copy_button = self.query_one("#copy-coordinate", Button)
        copy_button.display = False
        copy_button.disabled = True

    def _copy_coordinate(self) -> None:
        if not self._valid or self._view != "preview" or self._copy_path_line is None:
            return
        try:
            self.app.copy_to_clipboard(self._copy_path_line)
        except Exception:  # noqa: BLE001 - clipboard availability depends on the terminal driver
            self.query_one("#copy-feedback", Static).update(
                Text(f"剪貼簿複製失敗，請手動複製：{self._copy_path_line}")
            )
            return
        self.query_one("#copy-feedback", Static).update("已複製路徑:行號至剪貼簿。")

    def _copy_retained_message(self) -> None:
        if not self._valid or self._view != "preview" or self._selected_group is None:
            return
        message = representative_message(self._selected_group)
        if message:
            self._copy_retained_text(message, "代表訊息")

    def _copy_full_stacktrace(self) -> None:
        if not self._valid or self._view != "preview" or self._selected_group is None:
            return
        stacktrace = retained_stacktrace(self._selected_group)
        if stacktrace.strip():
            self._copy_retained_text(stacktrace, "完整堆疊")

    def _copy_retained_text(self, value: str, label: str) -> None:
        try:
            self.app.copy_to_clipboard(value)
        except Exception:  # noqa: BLE001 - clipboard availability depends on the terminal driver
            self.query_one("#copy-feedback", Static).update(
                f"剪貼簿複製失敗；請在上方保留的群組代表{label}中手動選取。"
            )
            return
        self.query_one("#copy-feedback", Static).update(
            f"已複製分析保留的群組代表{label}；不代表已核對原文。"
        )

    @staticmethod
    def _group_label(group: LogEntry) -> Text:
        text = Text()
        level = group.get("level")
        level_text = level if isinstance(level, str) and level else "未知層級"
        count = group.get("count", 1)
        count_text = (
            str(count)
            if isinstance(count, int) and not isinstance(count, bool)
            else "?"
        )
        filename = group.get("filename")
        filename_text = (
            filename if isinstance(filename, str) and filename else "未知檔案"
        )
        line_numbers = group.get("line_numbers", [])
        first_line = (
            line_numbers[0]
            if isinstance(line_numbers, list)
            and line_numbers
            and isinstance(line_numbers[0], (int, str))
            and not isinstance(line_numbers[0], bool)
            else "?"
        )
        logger = group.get("logger")
        logger_text = logger if isinstance(logger, str) and logger else "未知記錄器"
        message = group.get("message_body")
        if not isinstance(message, str) or not message:
            message = group.get("message")
        message_text = (
            message if isinstance(message, str) and message else "（無保留訊息）"
        )

        EventBrowserScreen._append_field(
            text,
            "層級 / 次數：",
            f"{level_text} / {count_text} 次\n",
        )
        EventBrowserScreen._append_field(
            text,
            "時間：",
            EventBrowserScreen._timestamp_range(group) + "\n",
        )
        EventBrowserScreen._append_field(text, "記錄器：", logger_text + "\n")
        EventBrowserScreen._append_field(text, "訊息：\n", message_text + "\n")
        EventBrowserScreen._append_field(
            text,
            "來源：",
            f"{filename_text}:{first_line}",
        )
        return text

    @staticmethod
    def _append_field(text: Text, label: str, value: str) -> None:
        text.append(label, style="bold cyan")
        text.append(value)

    @staticmethod
    def _timestamp_range(group: LogEntry) -> str:
        timestamp = group.get("timestamp")
        last_timestamp = group.get("last_timestamp")
        first_text = timestamp if isinstance(timestamp, str) and timestamp else ""
        last_text = (
            last_timestamp if isinstance(last_timestamp, str) and last_timestamp else ""
        )
        if first_text and last_text and first_text != last_text:
            return f"{first_text} → {last_text}"
        return first_text or last_text or "未知時間"

    @staticmethod
    def _occurrence_label(occurrence: OccurrenceCoordinate) -> Text:
        filename = occurrence.get("filename")
        line_number = occurrence.get("line_number")
        timestamp = occurrence.get("timestamp")
        thread = occurrence.get("thread")
        filename_text = (
            filename if isinstance(filename, str) and filename else "未知檔案"
        )
        line_text = (
            str(line_number)
            if isinstance(line_number, int) and not isinstance(line_number, bool)
            else "?"
        )
        timestamp_text = (
            timestamp if isinstance(timestamp, str) and timestamp else "未知時間"
        )
        thread_text = thread if isinstance(thread, str) and thread else "未知執行緒"
        return Text(
            f"{filename_text}:{line_text}｜時間 {timestamp_text}｜執行緒 {thread_text}"
        )

    def _occurrence_sort_key(self, occurrence_index: int) -> tuple[str, str, int]:
        occurrence = self._occurrences[occurrence_index]
        if not isinstance(occurrence, dict):
            return "", "", -1
        timestamp = occurrence.get("timestamp")
        filename = occurrence.get("filename")
        line_number = occurrence.get("line_number")
        timestamp_text = timestamp if isinstance(timestamp, str) else ""
        filename_text = filename if isinstance(filename, str) else ""
        line_value = (
            line_number
            if isinstance(line_number, int) and not isinstance(line_number, bool)
            else -1
        )
        return timestamp_text, filename_text, line_value

    def _sort_hint(self) -> str:
        if self._view == "occurrences":
            return "排序：本群組內依時間由早到晚"
        if self._query_sort == "count":
            return "瀏覽排序：出現次數最多"
        if self._query_sort == "recent":
            return "瀏覽排序：最近出現"
        if getattr(self.result, "sort_by", "time") == "level":
            return "排序：層級順序，再依時間由早到晚"
        return "排序：時間由早到晚"

    @staticmethod
    def _retained_details_text(
        group: LogEntry,
        occurrence: OccurrenceCoordinate,
        *,
        stacktrace_preview: str | None = None,
    ) -> Text:
        """組合出現位置與分析保留的代表資訊，供預覽畫面顯示。

        Args:
            group: 分析結果保留的事件群組。
            occurrence: 目前選取的來源座標。
            stacktrace_preview: 指定顯示的堆疊文字；未提供時顯示完整堆疊。

        Returns:
            含欄位標籤樣式的代表資訊文字。
        """
        text = Text()

        text.append("選取的出現位置\n", style="bold cyan")
        timestamp = occurrence.get("timestamp")
        thread = occurrence.get("thread")
        filename = occurrence.get("filename")
        line_number = occurrence.get("line_number")
        timestamp_text = (
            timestamp if isinstance(timestamp, str) and timestamp else "未知時間"
        )
        thread_text = thread if isinstance(thread, str) and thread else "未知執行緒"
        filename_text = (
            filename if isinstance(filename, str) and filename else "未知檔案"
        )
        line_text = (
            str(line_number)
            if isinstance(line_number, int) and not isinstance(line_number, bool)
            else "?"
        )
        EventBrowserScreen._append_field(text, "時間：", timestamp_text + "\n")
        EventBrowserScreen._append_field(text, "執行緒：", thread_text + "\n")
        EventBrowserScreen._append_field(
            text, "座標：", f"{filename_text}:{line_text}\n\n"
        )

        text.append("事件群組資訊\n", style="bold cyan")

        level = group.get("level")
        level_text = level if isinstance(level, str) and level else "未知層級"
        logger = group.get("logger")
        logger_text = logger if isinstance(logger, str) and logger else "未知記錄器"
        message_text = representative_message(group) or "（無保留訊息）"
        stacktrace = (
            retained_stacktrace(group)
            if stacktrace_preview is None
            else stacktrace_preview
        )
        stacktrace_text = stacktrace or "（無保留堆疊追蹤）"

        EventBrowserScreen._append_field(text, "層級：", level_text + "\n")
        EventBrowserScreen._append_field(
            text,
            "時間範圍：",
            EventBrowserScreen._timestamp_range(group) + "\n",
        )
        EventBrowserScreen._append_field(text, "記錄器：", logger_text + "\n\n")
        text.append(
            "分析保留的群組代表（可能不是目前選取位置的原始內容）\n",
            style="bold yellow",
        )
        EventBrowserScreen._append_field(text, "訊息：\n", message_text + "\n")
        EventBrowserScreen._append_field(text, "堆疊追蹤：\n", stacktrace_text)
        return text

    @staticmethod
    def _context_text(preview: SourcePreview) -> Text:
        text = Text()
        if preview.warning is not None or not preview.lines:
            return text
        text.append("已核對的原文前後文\n", style="bold cyan")
        for index, (line_number, content) in enumerate(preview.lines):
            if index:
                text.append("\n")
            style = "bold cyan" if line_number == preview.match_line else "dim"
            text.append(f"{line_number:>6}  ", style=style)
            text.append(
                content,
                style="bold yellow" if line_number == preview.match_line else "",
            )
        return text
