from __future__ import annotations

import asyncio
from collections.abc import Mapping

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from log_analyzer.application.analysis.service import AnalysisResult
from log_analyzer.application.events.similarity import (
    SimilarGroup,
    find_similar_groups,
    message_text,
)

_PAGE_SIZE = 100


class EventSimilarityScreen(ModalScreen[None]):
    """查找分析結果中的相似訊息並提供原事件瀏覽入口。"""

    def __init__(
        self,
        result: AnalysisResult,
        reference: str,
        *,
        exclude_index: int | None = None,
    ) -> None:
        """建立一次以參考訊息為起點的候選查找畫面。

        Args:
            result: 仍有效且保留事件群組的分析結果。
            reference: 預先帶入、之後仍可編輯的參考訊息。
            exclude_index: 參考事件在原分析結果中的群組索引。
        """
        self.result = result
        self.reference = reference
        self.exclude_index = exclude_index
        self._matches: list[SimilarGroup] = []
        self._page_start = 0
        self._applied_query: tuple[str, float] | None = None
        self._query_task: asyncio.Task | None = None
        self._querying = False
        self._valid = True
        super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="similarity-dialog"), Container(id="similarity-card"):
            yield Static("尋找相似事件", id="similarity-title")
            yield Static(
                "比對訊息正文；不包含時間、Level、Logger、來源座標或堆疊。"
                "分數代表文字相似度，不代表根因機率。",
                id="similarity-hint",
            )
            with Container(id="similarity-controls"):
                yield Input(
                    value=self.reference,
                    placeholder="輸入參考訊息",
                    id="similarity-reference",
                )
                with Container(id="similarity-search-controls"):
                    yield Input(
                        value="80",
                        placeholder="門檻 0–100",
                        type="number",
                        id="similarity-threshold",
                    )
                    yield Button("尋找", id="similarity-search")
            yield Static("", id="similarity-status")
            yield Static("", id="similarity-page")
            yield OptionList(id="similarity-options")
            with Container(id="similarity-actions"):
                yield Button("上一頁", id="similarity-previous", disabled=True)
                yield Button("下一頁", id="similarity-next", disabled=True)
                yield Button("返回", id="similarity-back")

    def on_mount(self) -> None:
        self._update_layout()
        self._start_search()

    def on_unmount(self) -> None:
        self._valid = False
        if self._query_task is not None and not self._query_task.done():
            self._query_task.cancel()

    def on_resize(self) -> None:
        if self.is_mounted:
            self._update_layout()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"similarity-reference", "similarity-threshold"}:
            event.stop()
            self._start_search()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "similarity-search": self._start_search,
            "similarity-previous": lambda: self._move_page(-1),
            "similarity-next": lambda: self._move_page(1),
            "similarity-back": self.dismiss,
        }
        handler = handlers.get(event.button.id)
        if handler is not None:
            handler()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self._valid or event.option_list.id != "similarity-options":
            return
        try:
            group_index = int(event.option.id or "")
        except ValueError:
            return
        page_matches = self._matches[self._page_start : self._page_start + _PAGE_SIZE]
        if not any(match.group_index == group_index for match in page_matches):
            return

        from log_analyzer.presentation.tui.screens.event_browser import (
            EventBrowserScreen,
        )

        self.app.push_screen(
            EventBrowserScreen(
                self.result,
                initial_group_index=group_index,
                initial_occurrence_index=0,
            )
        )

    def _update_layout(self) -> None:
        wide = self.size.width >= 100
        self.query_one("#similarity-controls").set_class(wide, "wide")
        self.query_one("#similarity-search-controls").set_class(wide, "wide")
        self.query_one("#similarity-card").set_class(self.size.height < 30, "short")

    def _start_search(self) -> None:
        if not self._valid or self._querying:
            return

        reference = self.query_one("#similarity-reference", Input).value
        threshold_text = self.query_one("#similarity-threshold", Input).value.strip()
        try:
            threshold = float(threshold_text)
        except ValueError:
            self._report_query_error("門檻必須是 0 到 100 之間的數字。")
            return

        self._querying = True
        self._set_busy(True)
        self._set_status("正在查找已分析結果…")
        self._query_task = asyncio.create_task(self._run_search(reference, threshold))

    async def _run_search(self, reference: str, threshold: float) -> None:
        task = asyncio.current_task()
        try:
            matches = await find_similar_groups(
                self.result.matched_logs,
                reference,
                threshold=threshold,
                exclude_index=self.exclude_index,
            )
        except asyncio.CancelledError:
            return
        except (OSError, TypeError, ValueError) as exc:
            if self._is_current_query(task):
                self._report_query_error(str(exc))
        else:
            if not self._is_current_query(task):
                return
            self._matches = matches
            self._page_start = 0
            self._applied_query = (reference, threshold)
            self._render_page()
            if matches:
                self._set_status(
                    f"找到 {len(matches)} 筆候選；分數只代表文字相似度。"
                )
            else:
                self._set_status("沒有符合門檻的相似事件。")
            self.query_one("#similarity-options", OptionList).focus()
        finally:
            if self._is_current_query(task):
                self._querying = False
                self._query_task = None
                self._set_busy(False)

    def _is_current_query(self, task: asyncio.Task | None) -> bool:
        return bool(
            self._valid
            and self.is_mounted
            and task is not None
            and self._query_task is task
        )

    def _set_busy(self, busy: bool) -> None:
        for selector in (
            "#similarity-reference",
            "#similarity-threshold",
            "#similarity-search",
        ):
            self.query_one(selector).disabled = busy
        options = self.query_one("#similarity-options", OptionList)
        options.disabled = busy
        self.query_one("#similarity-previous", Button).disabled = (
            busy or self._page_start <= 0
        )
        self.query_one("#similarity-next", Button).disabled = (
            busy or self._page_start + _PAGE_SIZE >= len(self._matches)
        )

    def _move_page(self, direction: int) -> None:
        if not self._valid or self._querying:
            return
        next_start = self._page_start + direction * _PAGE_SIZE
        if 0 <= next_start < len(self._matches):
            self._page_start = next_start
            self._render_page()

    def _render_page(self) -> None:
        page_matches = self._matches[self._page_start : self._page_start + _PAGE_SIZE]
        options = [
            Option(self._candidate_label(match), id=str(match.group_index))
            for match in page_matches
        ]
        option_list = self.query_one("#similarity-options", OptionList)
        option_list.clear_options()
        option_list.add_options(options)
        if options:
            option_list.highlighted = 0

        total = len(self._matches)
        first = self._page_start + 1 if total else 0
        last = min(self._page_start + _PAGE_SIZE, total)
        self.query_one("#similarity-page", Static).update(
            f"候選 {first}–{last}/{total}"
        )
        self._set_busy(False)

    def _candidate_label(self, match: SimilarGroup) -> Text:
        group = self.result.matched_logs[match.group_index]
        level = group.get("level")
        level_text = level if isinstance(level, str) and level else "未知"
        occurrences = group.get("occurrences")
        count = group.get("count")
        if not isinstance(count, int):
            count = len(occurrences) if isinstance(occurrences, list) else 0
        source = _source_label(group)
        message = message_text(group)
        if len(message) > 120:
            message = f"{message[:117]}…"

        label = Text()
        label.append(f"{match.score:.1f}%  {level_text}  {count} 次  {source}\n", style="bold")
        label.append(message)
        return label

    def _set_status(self, message: str, *, error: bool = False) -> None:
        status = Text(message)
        if error:
            status.stylize("bold red")
        self.query_one("#similarity-status", Static).update(status)

    def _report_query_error(self, message: str) -> None:
        if self._applied_query is None:
            self._set_status(f"查找失敗：{message}", error=True)
            return
        reference, threshold = self._applied_query
        reference_label = reference if len(reference) <= 40 else f"{reference[:37]}…"
        self._set_status(
            f"查找失敗：{message}；保留上次查詢「{reference_label}」與門檻 {threshold:g} 的 "
            f"{len(self._matches)} 筆結果。",
            error=True,
        )


def _source_label(group: Mapping[str, object]) -> str:
    occurrences = group.get("occurrences")
    if isinstance(occurrences, list) and occurrences:
        occurrence = occurrences[0]
        if isinstance(occurrence, Mapping):
            filename = occurrence.get("filename")
            line_number = occurrence.get("line_number")
            if isinstance(filename, str) and isinstance(line_number, int):
                return f"{filename}:{line_number}"

    filename = group.get("filename")
    return filename if isinstance(filename, str) and filename else "來源未知"
