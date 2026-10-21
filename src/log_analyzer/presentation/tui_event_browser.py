from __future__ import annotations

from math import ceil

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from ..application.analysis_service import AnalysisResult
from ..application.source_preview import SourcePreview, read_source_context
from ..domain.log_types import LogEntry, OccurrenceCoordinate


class EventBrowserScreen(ModalScreen[None]):
    """Browse matched groups and their verified source coordinates."""

    def __init__(self, result: AnalysisResult, page_size: int = 100) -> None:
        self.result = result
        self.page_size = min(max(1, int(page_size)), 100)
        self._view = "groups"
        self._valid = True
        self._group_start = 0
        self._group_page: list[LogEntry] = []
        self._selected_group: LogEntry | None = None
        self._occurrences: list[OccurrenceCoordinate] = []
        self._occurrence_start = 0
        self._current_preview: SourcePreview | None = None
        self._copy_path_line: str | None = None
        super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="event-browser"), Container(id="event-browser-card"):
            yield Static("事件瀏覽", id="browser-title")
            yield Static("", id="browser-page")
            yield OptionList(id="event-options")
            yield Static("", id="source-warning")
            yield Static("", id="source-path")
            yield Static("", id="source-context")
            yield Static("", id="copy-feedback")
            with Container(id="event-browser-actions"):
                yield Button("上一頁", id="browser-previous")
                yield Button("下一頁", id="browser-next")
                yield Button("返回", id="browser-back")
                yield Button("複製路徑:行號", id="copy-coordinate", disabled=True)
                yield Button("關閉", id="close-event-browser")

    def on_mount(self) -> None:
        self._render_group_page()
        self.query_one("#event-options", OptionList).focus()

    def on_unmount(self) -> None:
        self.invalidate()

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
            occurrences = self._selected_group.get("occurrences")
            self._occurrences = occurrences if isinstance(occurrences, list) else []
            self._occurrence_start = 0
            self._render_occurrence_page()
        elif self._view == "occurrences":
            absolute_index = self._occurrence_start + page_index
            if absolute_index < 0 or absolute_index >= len(self._occurrences):
                return
            self._render_preview(self._occurrences[absolute_index])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "close-event-browser":
            self.dismiss()
        elif button_id == "browser-previous":
            self._move_page(-1)
        elif button_id == "browser-next":
            self._move_page(1)
        elif button_id == "browser-back":
            if self._view == "preview":
                self._render_occurrence_page()
            elif self._view == "occurrences":
                self._render_group_page()
        elif button_id == "copy-coordinate":
            self._copy_coordinate()

    def invalidate(self) -> None:
        """Prevent cached coordinates from being used after their result is released."""
        self._valid = False
        self._group_page.clear()
        self._selected_group = None
        self._occurrences.clear()
        self._current_preview = None
        self._copy_path_line = None

    def _move_page(self, direction: int) -> None:
        if not self._valid:
            return
        if self._view == "groups":
            length = len(self.result.matched_logs)
            next_start = self._group_start + direction * self.page_size
            if 0 <= next_start < length:
                self._group_start = next_start
                self._render_group_page()
        elif self._view == "occurrences":
            next_start = self._occurrence_start + direction * self.page_size
            if 0 <= next_start < len(self._occurrences):
                self._occurrence_start = next_start
                self._render_occurrence_page()

    def _render_group_page(self) -> None:
        if not self._valid:
            return
        self._view = "groups"
        store = self.result.matched_logs
        length = len(store)
        iter_page = getattr(store, "iter_page", None)
        if callable(iter_page):
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

    def _render_occurrence_page(self) -> None:
        if not self._valid or self._selected_group is None:
            return
        self._view = "occurrences"
        self._current_preview = None
        self._copy_path_line = None
        length = len(self._occurrences)
        page = self._occurrences[self._occurrence_start : self._occurrence_start + self.page_size]
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

    def _render_preview(self, occurrence: OccurrenceCoordinate) -> None:
        if not self._valid:
            return
        filename = occurrence.get("filename", "")
        source_states = getattr(self.result, "source_states", {})
        snapshot = source_states.get(filename) if isinstance(filename, str) else None
        preview = read_source_context(self.result.input_path, occurrence, snapshot)
        self._view = "preview"
        self._current_preview = preview
        self._copy_path_line = preview.path_line if preview.warning is None and preview.lines else None

        self.query_one("#browser-title", Static).update("來源內容預覽")
        self.query_one("#browser-page", Static).update("")
        self.query_one("#event-options", OptionList).display = False
        self.query_one("#browser-previous", Button).display = False
        self.query_one("#browser-next", Button).display = False
        back_button = self.query_one("#browser-back", Button)
        back_button.display = True
        back_button.label = "返回事件出現位置"
        warning_widget = self.query_one("#source-warning", Static)
        warning_widget.display = bool(preview.warning)
        warning_widget.update(preview.warning or "")
        self.query_one("#source-path", Static).display = True
        self.query_one("#source-path", Static).update(Text(preview.path_line or "", no_wrap=False))
        self.query_one("#source-context", Static).display = True
        self.query_one("#source-context", Static).update(self._context_text(preview))
        self.query_one("#copy-feedback", Static).display = True
        self.query_one("#copy-feedback", Static).update("")
        copy_button = self.query_one("#copy-coordinate", Button)
        copy_button.display = True
        copy_button.disabled = self._copy_path_line is None

    def _set_list_state(self, title: str, options: list[Option], start: int, length: int) -> None:
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
        event_options = self.query_one("#event-options", OptionList)
        event_options.set_options(options)
        event_options.highlighted = 0 if options else None
        event_options.display = True
        self.query_one("#browser-previous", Button).display = True
        self.query_one("#browser-next", Button).display = True
        self.query_one("#browser-previous", Button).disabled = start <= 0
        self.query_one("#browser-next", Button).disabled = start + self.page_size >= length
        back_button = self.query_one("#browser-back", Button)
        back_button.display = self._view != "groups"
        back_button.label = "返回事件群組" if self._view == "occurrences" else "返回"
        self.query_one("#source-warning", Static).display = False
        self.query_one("#source-path", Static).display = False
        self.query_one("#source-context", Static).display = False
        self.query_one("#copy-feedback", Static).display = False
        self.query_one("#copy-coordinate", Button).display = False

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
    def _group_label(group: LogEntry) -> str:
        level = str(group.get("level", "未知層級"))
        count = group.get("count", 1)
        filename = str(group.get("filename", "未知檔案"))
        line_numbers = group.get("line_numbers", [])
        first_line = line_numbers[0] if line_numbers else "?"
        logger = str(group.get("logger", ""))
        message = str(group.get("message_body", group.get("message", ""))).replace("\n", " ")
        return f"{level}（{count} 次）｜{filename}:{first_line}｜{logger}｜{message}"

    @staticmethod
    def _occurrence_label(occurrence: OccurrenceCoordinate) -> str:
        filename = occurrence.get("filename", "未知檔案")
        line_number = occurrence.get("line_number", "?")
        timestamp = occurrence.get("timestamp", "")
        thread = occurrence.get("thread", "")
        return f"{filename}:{line_number}｜時間 {timestamp}｜執行緒 {thread}"

    @staticmethod
    def _context_text(preview: SourcePreview) -> Text:
        text = Text()
        for index, (line_number, content) in enumerate(preview.lines):
            if index:
                text.append("\n")
            style = "bold cyan" if line_number == preview.match_line else "dim"
            text.append(f"{line_number:>6}  ", style=style)
            text.append(content, style="bold yellow" if line_number == preview.match_line else "")
        return text
