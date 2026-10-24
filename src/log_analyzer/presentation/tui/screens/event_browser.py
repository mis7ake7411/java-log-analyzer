from __future__ import annotations
import asyncio
from math import ceil
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option
from log_analyzer.application.analysis_service import AnalysisResult
from log_analyzer.application.event_query import find_group_indices
from log_analyzer.application.source_context_index import SourceContextReader
from log_analyzer.application.source_preview import SourcePreview, read_source_context
from log_analyzer.domain.log_types import LogEntry, OccurrenceCoordinate


class EventBrowserScreen(ModalScreen[None]):
    """Browse matched groups and their verified source coordinates."""

    def __init__(self, result: AnalysisResult, page_size: int = 100) -> None:
        self.result = result
        self.page_size = min(max(1, int(page_size)), 100)
        self._view = "groups"
        self._valid = True
        self._group_start = 0
        self._group_indices: list[int] | None = None
        self._group_highlight = 0
        self._occurrence_highlight = 0
        self._query_sort = "original"
        self._query_task: asyncio.Task | None = None
        self._querying = False
        self._group_page: list[LogEntry] = []
        self._selected_group: LogEntry | None = None
        self._occurrences: list[OccurrenceCoordinate] = []
        self._occurrence_order: list[int] = []
        self._occurrence_start = 0
        self._source_context_reader = SourceContextReader()
        self._current_preview: SourcePreview | None = None
        self._copy_path_line: str | None = None
        super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="event-browser"), Container(id="event-browser-card"):
            yield Static("事件瀏覽", id="browser-title")
            yield Static("", id="browser-page")
            yield Static("", id="browser-sort-hint")
            with Container(id="browser-controls"):
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
                    yield Button("清除", id="browser-clear")
            yield Static("", id="browser-query-status")
            yield OptionList(id="event-options")
            with VerticalScroll(id="browser-preview-content"):
                yield Static("", id="source-warning")
                yield Static("", id="source-encoding-notice")
                yield Static("", id="source-path")
                yield Static("", id="retained-details")
                yield Static("", id="source-context")
                yield Static("", id="copy-feedback")
            with Container(id="event-browser-actions"):
                yield Button("上一頁", id="browser-previous")
                yield Button("下一頁", id="browser-next")
                yield Button("返回", id="browser-back")
                yield Button("複製路徑:行號", id="copy-coordinate", disabled=True)
                yield Button("關閉", id="close-event-browser")

    def on_mount(self) -> None:
        self._update_browser_layout()
        self._render_group_page()
        self.query_one("#event-options", OptionList).focus()

    def on_unmount(self) -> None:
        self.invalidate()

    def on_resize(self) -> None:
        if self.is_mounted:
            self._update_browser_layout()

    def _update_browser_layout(self) -> None:
        self.query_one("#browser-controls").set_class(self.size.width >= 100, "wide")
        self.query_one("#event-browser-card").set_class(self.size.height < 30, "short")
        self.query_one("#browser-sort-hint").display = (
            self._view != "preview" and self.size.height >= 30
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "browser-query":
            event.stop()
            self._start_query()

    def _start_query(self) -> None:
        if self._valid and not self._querying and self._view == "groups":
            self._querying = True
            self._query_task = asyncio.create_task(self._apply_group_query())

    async def _apply_group_query(self) -> None:
        search = self.query_one("#browser-query", Input)
        order = self.query_one("#browser-order", Select)
        controls = [
            search,
            order,
            self.query_one("#browser-apply"),
            self.query_one("#browser-clear"),
        ]
        query, sort_by = search.value.strip(), str(order.value)
        status = self.query_one("#browser-query-status", Static)
        status.update("正在搜尋已分析結果…")
        for widget in controls:
            widget.disabled = True
        self.query_one("#event-options").disabled = True
        self.query_one("#browser-previous").disabled = True
        self.query_one("#browser-next").disabled = True
        previous = (
            self._group_indices,
            self._query_sort,
            self._group_start,
            self._group_highlight,
        )
        try:
            indices = (
                None
                if not query and sort_by == "original"
                else await find_group_indices(self.result.matched_logs, query, sort_by)
            )
            if not self._valid:
                return
            self._group_indices = indices
            self._query_sort = sort_by
            self._group_start = self._group_highlight = 0
            self._render_group_page()
            count = len(self.result.matched_logs) if indices is None else len(indices)
            status.update(
                f"瀏覽 {count}/{len(self.result.matched_logs)} 群組；分析結果與匯出內容未變更。"
                if indices is not None
                else ""
            )
        except (OSError, ValueError, EOFError) as exc:
            (
                self._group_indices,
                self._query_sort,
                self._group_start,
                self._group_highlight,
            ) = previous
            status.update(f"無法搜尋：{exc}")
        finally:
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
        button_id = event.button.id
        if button_id == "close-event-browser":
            self.dismiss()
        elif button_id == "browser-apply":
            self._start_query()
        elif button_id == "browser-clear":
            self.query_one("#browser-query", Input).value = ""
            self.query_one("#browser-order", Select).value = "original"
            self._start_query()
        elif button_id == "browser-previous":
            self._move_page(-1)
        elif button_id == "browser-next":
            self._move_page(1)
        elif button_id == "browser-back":
            if self._view == "preview":
                self._render_occurrence_page()
            elif self._view == "occurrences":
                self._render_group_page()
            self.query_one("#event-options").focus()
        elif button_id == "copy-coordinate":
            self._copy_coordinate()

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

        options = [
            Option(self._group_label(group), id=str(page_index))
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

    def _render_occurrence_page(self) -> None:
        if not self._valid or self._selected_group is None:
            return
        self._view = "occurrences"
        self._current_preview = None
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
        retained_details = self.query_one("#retained-details", Static)
        retained_details.display = True
        if self._selected_group is not None:
            retained_details.update(
                self._retained_details_text(self._selected_group, occurrence)
            )
        context_widget = self.query_one("#source-context", Static)
        has_verified_context = preview.warning is None and bool(preview.lines)
        context_widget.display = has_verified_context
        context_widget.update(
            self._context_text(preview) if has_verified_context else Text()
        )
        self.query_one("#copy-feedback", Static).display = True
        self.query_one("#copy-feedback", Static).update("")
        copy_button = self.query_one("#copy-coordinate", Button)
        copy_button.display = True
        copy_button.disabled = self._copy_path_line is None
        self.query_one("#browser-preview-content", VerticalScroll).scroll_home(
            animate=False
        )

    def _set_list_state(
        self, title: str, options: list[Option], start: int, length: int
    ) -> None:
        self.query_one("#browser-controls").display = self._view == "groups"
        self.query_one("#browser-query-status").display = self._view == "groups"
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
        if not isinstance(message, str):
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
        group: LogEntry, occurrence: OccurrenceCoordinate
    ) -> Text:
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
        message = group.get("message_body")
        if not isinstance(message, str):
            message = group.get("message")
        message_text = (
            message if isinstance(message, str) and message else "（無保留訊息）"
        )
        stacktrace = group.get("stacktrace")
        if not isinstance(stacktrace, str) or not stacktrace:
            stacktrace_lines = group.get("stacktrace_lines")
            stacktrace = (
                "\n".join(line for line in stacktrace_lines if isinstance(line, str))
                if isinstance(stacktrace_lines, list)
                else ""
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
