"""可重用的即時事件清單與追蹤控制元件。"""

from __future__ import annotations

import unicodedata
from datetime import timezone
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.events import MouseScrollUp
from textual.message import Message
from textual.widgets import Button, Checkbox, OptionList, Static, Switch
from textual.widgets.option_list import Option

from log_analyzer.application.live.session import LiveSession


_PAGE_SIZE = 100
_REBUILD_WARNING = "從目前來源重建，成功後取代本次追蹤結果"
_KNOWN_LEVELS = ("ERROR", "WARN", "WARNING", "INFO", "DEBUG", "TRACE")
_STATE_LABELS = {
    "tracking": "追蹤中",
    "paused": "已暫停",
    "interrupted": "來源中斷",
    "error": "錯誤",
    "stopped": "已結束",
}


class LivePanel(Vertical):
    """呈現即時狀態與分頁事件，並將所有控制請求交給外層處理。

    元件只讀取工作階段的狀態與已保存事件，不主動讀取來源或推進分析。
    """

    class ToggleTracking(Message):
        """要求外層開始／恢復或暫停追蹤。"""

        def __init__(self, enabled: bool) -> None:
            self.enabled = enabled
            super().__init__()

    class RebuildRequested(Message):
        """要求外層依目前條件重新分析來源。"""

    class SummaryRequested(Message):
        """要求外層開啟目前追蹤摘要。"""

    class StopRequested(Message):
        """要求外層明確結束目前追蹤。"""

    class OpenEvent(Message):
        """交付使用者選取的事件，供外層開啟原文檢視器。

        Attributes:
            event: 已捕捉的事件資料，包含原始文字與接收識別資訊。
        """

        def __init__(self, event: dict[str, Any]) -> None:
            self.event = dict(event)
            super().__init__()

    def __init__(self, *, id: str = "live-panel") -> None:
        """建立空白的即時面板。

        Args:
            id: 面板根元件的識別碼。
        """
        super().__init__(id=id)
        self._session: LiveSession | None = None
        self._page_index = 0
        self._matched = 0
        self._last_matched = 0
        self._new_count = 0
        self._following = True
        self._busy = False
        self._session_epoch = 0
        self._visible_events: dict[str, dict[str, Any]] = {}
        self._programmatic_highlights: list[int] = []
        self._programmatic_switch_values: list[bool] = []
        self._programmatic_follow_values: list[bool] = []
        self._highlight_override: int | None = None
        self._reset_scroll_on_render = False

    def compose(self) -> ComposeResult:
        """組合操作列、狀態摘要與可捲動的事件清單。

        Returns:
            ComposeResult: Textual 要掛載的面板元件。
        """
        with Horizontal(id="live-actions"):
            yield Static("追蹤", id="live-switch-label", markup=False)
            yield Switch(id="live-switch", value=False)
            yield Button("重新分析來源", id="live-rebuild", disabled=True)
        with ScrollableContainer(id="live-content-scroll"):
            yield Static(_REBUILD_WARNING, id="live-rebuild-warning", markup=False)
            with Horizontal(id="live-actions-secondary"):
                yield Button("查看追蹤摘要", id="live-summary", disabled=True)
                yield Button("結束追蹤", id="live-stop", disabled=True)
            with Horizontal(id="live-reading-controls"):
                yield Checkbox("跟隨最新", id="live-follow", value=True)
                yield Button("上一頁", id="live-previous", disabled=True)
                yield Button("下一頁", id="live-next", disabled=True)
                yield Static("1 / 1", id="live-page", markup=False)
            yield Static("跟隨最新", id="live-new-events", markup=False)
            yield Static("", id="live-status", markup=False)
            yield OptionList(id="live-events", markup=False)

    def on_mount(self) -> None:
        """掛載完成後同步目前工作階段資料。"""
        self.call_after_refresh(self.refresh_session)

    def set_session(self, session: LiveSession | None) -> None:
        """替換資料來源；更換工作階段時清除舊分頁與事件索引。

        Args:
            session: 新的工作階段，None 表示清空面板。

        Returns:
            None: 更新面板目前資料來源。
        """
        if session is not self._session:
            self._session = session
            self._session_epoch += 1
            self._page_index = 0
            self._last_matched = 0 if session is None else session.matched
            self._new_count = 0
            self._visible_events.clear()
            self._highlight_override = 0 if session is not None else None
            self._reset_scroll_on_render = True
        if self.is_mounted:
            self.refresh_session()

    def refresh_session(self) -> None:
        """只同步工作階段狀態與目前頁面，不推進讀取或完成分析。

        Returns:
            None: 讀取狀態並更新面板元件。
        """
        if not self.is_mounted:
            return

        session = self._session
        previous_highlighted = self.query_one("#live-events", OptionList).highlighted
        previous_event = self._event_for_highlight(previous_highlighted)

        if session is None:
            self._matched = 0
            self._page_index = 0
            self._new_count = 0
            self._visible_events.clear()
            self._render_events((), None)
            self._render_status(None)
            self._sync_controls(None)
            self._render_page_indicator()
            self._render_new_count()
            return

        matched = max(0, int(session.matched))
        if matched > self._last_matched and not self._following:
            self._new_count += matched - self._last_matched
        elif matched < self._last_matched:
            self._new_count = 0
        self._last_matched = matched
        self._matched = matched

        page_count = max(1, (matched + _PAGE_SIZE - 1) // _PAGE_SIZE)
        if self._following:
            self._page_index = page_count - 1
            self._new_count = 0
        else:
            self._page_index = min(self._page_index, page_count - 1)

        events = list(session.iter_page(self._page_index * _PAGE_SIZE, _PAGE_SIZE))
        highlight = self._resolve_highlight(
            events, previous_event, previous_highlighted
        )
        self._render_events(events, highlight)
        self._render_status(session)
        self._sync_controls(session)
        self._render_page_indicator(page_count)
        self._render_new_count()

    def set_busy(self, busy: bool) -> None:
        """設定外層操作交接狀態；事件瀏覽與分頁仍可使用。

        Args:
            busy: True 時暫停可能變更追蹤狀態的操作。

        Returns:
            None: 更新操作控制項的停用狀態。
        """
        self._busy = bool(busy)
        if self.is_mounted:
            self._sync_controls(self._session)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id != "live-switch":
            return
        if self._consume_suppressed(self._programmatic_switch_values, event.value):
            return
        self.post_message(self.ToggleTracking(event.value))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id != "live-follow":
            return
        if self._consume_suppressed(self._programmatic_follow_values, event.value):
            return
        self._following = event.value
        self._new_count = 0
        self._highlight_override = None
        self.refresh_session()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id not in {
            "live-rebuild",
            "live-summary",
            "live-stop",
            "live-previous",
            "live-next",
        }:
            return
        event.stop()
        if button_id == "live-rebuild":
            self.post_message(self.RebuildRequested())
        elif button_id == "live-summary":
            self.post_message(self.SummaryRequested())
        elif button_id == "live-stop":
            self.post_message(self.StopRequested())
        elif button_id == "live-previous":
            self._move_page(-1)
        else:
            self._move_page(1)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id != "live-events":
            return
        if self._consume_suppressed(self._programmatic_highlights, event.option_index):
            return
        self._set_following(False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "live-events":
            return
        event.stop()
        captured = self._visible_events.get(event.option.id or "")
        if captured is not None:
            self.post_message(self.OpenEvent(captured))

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        if event.widget is not None and event.widget.id == "live-events":
            self._set_following(False)

    def _move_page(self, delta: int) -> None:
        page_count = max(1, (self._matched + _PAGE_SIZE - 1) // _PAGE_SIZE)
        target = min(max(0, self._page_index + delta), page_count - 1)
        if target == self._page_index:
            return
        self._set_following(False)
        self._page_index = target
        self._highlight_override = 0
        self._reset_scroll_on_render = True
        self.refresh_session()

    def _set_following(self, following: bool) -> None:
        self._following = following
        if following:
            self._new_count = 0
        if not self.is_mounted:
            return
        checkbox = self.query_one("#live-follow", Checkbox)
        if checkbox.value != following:
            self._programmatic_follow_values.append(following)
            checkbox.value = following
        self._render_new_count()

    def _event_for_highlight(self, highlighted: int | None) -> dict[str, Any] | None:
        if highlighted is None:
            return None
        options = self.query_one("#live-events", OptionList)
        if not 0 <= highlighted < len(options.options):
            return None
        return self._visible_events.get(options.options[highlighted].id or "")

    def _resolve_highlight(
        self,
        events: list[dict[str, Any]],
        previous_event: dict[str, Any] | None,
        previous_highlighted: int | None,
    ) -> int | None:
        if not events:
            self._highlight_override = None
            return None
        if self._following:
            self._highlight_override = None
            return len(events) - 1
        if self._highlight_override is not None:
            selected = min(self._highlight_override, len(events) - 1)
            self._highlight_override = None
            return selected
        if previous_event is not None:
            identity = (
                previous_event.get("sequence"),
                previous_event.get("generation"),
            )
            for index, event in enumerate(events):
                if (event.get("sequence"), event.get("generation")) == identity:
                    return index
        if previous_highlighted is not None:
            return min(previous_highlighted, len(events) - 1)
        return None

    def _render_events(
        self,
        events: tuple[dict[str, Any], ...] | list[dict[str, Any]],
        highlight: int | None,
    ) -> None:
        option_list = self.query_one("#live-events", OptionList)
        desired = [
            (self._event_option_id(event), self._event_label(event), event)
            for event in events
        ]
        current_options = tuple(option_list.options)
        current_ids = tuple(option.id for option in current_options)
        desired_ids = tuple(option_id for option_id, _label, _event in desired)
        desired_labels = tuple(label for _option_id, label, _event in desired)
        current_labels = tuple(str(option.prompt) for option in current_options)
        visible = {option_id: dict(event) for option_id, _label, event in desired}

        if current_ids == desired_ids and current_labels == desired_labels:
            self._visible_events = visible
            option_list.disabled = not bool(events)
            self._set_option_highlight(option_list, highlight)
            self._reset_scroll_on_render = False
            return

        if (
            len(current_ids) < len(desired_ids)
            and desired_ids[: len(current_ids)] == current_ids
            and current_labels == desired_labels[: len(current_labels)]
        ):
            for option_id, label, _event in desired[len(current_ids) :]:
                option_list.add_option(Option(label, id=option_id))
            self._visible_events = visible
            option_list.disabled = not bool(events)
            self._set_option_highlight(option_list, highlight)
            self._reset_scroll_on_render = False
            return

        previous_scroll_y = option_list.scroll_y
        option_list.clear_options()
        for option_id, label, _event in desired:
            option_list.add_option(Option(label, id=option_id))
        self._visible_events = visible
        option_list.disabled = not bool(events)
        self._set_option_highlight(option_list, highlight)
        if not self._following and not self._reset_scroll_on_render:
            option_list.scroll_to(y=previous_scroll_y, animate=False, immediate=True)
        self._reset_scroll_on_render = False

    def _set_option_highlight(
        self, option_list: OptionList, highlight: int | None
    ) -> None:
        if highlight is not None and option_list.highlighted != highlight:
            self._programmatic_highlights.append(highlight)
            option_list.highlighted = highlight

    def _event_option_id(self, event: dict[str, Any]) -> str:
        return (
            f"{self._session_epoch}:{event.get('generation', 0)}:"
            f"{event.get('sequence', '')}"
        )

    def _render_status(self, session: LiveSession | None) -> None:
        status = self.query_one("#live-status", Static)
        if session is None:
            completed = matched = received = backlog = 0
            pending = False
            state_label = "尚未開始追蹤"
            last_read_at = None
            level_counts = {}
            gaps: tuple[str, ...] = ()
            error = ""
        else:
            completed = int(session.completed)
            matched = int(session.matched)
            received = int(session.received_bytes)
            backlog = int(session.backlog)
            pending = bool(session.has_pending)
            state_label = _STATE_LABELS.get(session.state, str(session.state))
            last_read_at = getattr(session, "last_read_at", None)
            level_counts = getattr(session, "level_counts", {})
            gaps = tuple(str(gap) for gap in session.gaps)
            error = str(session.error) if session.error else ""

        if last_read_at is None:
            last_read_text = "尚無"
        else:
            if last_read_at.tzinfo is None:
                last_read_at = last_read_at.replace(tzinfo=timezone.utc)
            last_read_text = last_read_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )

        width = max(36, status.region.width)
        level_lines = self._format_level_counts(level_counts, width)
        gap_text = "無"
        if gaps:
            gap_text = self._truncate_to_cells(
                gaps[0], max(8, width - self._cell_width(f"缺口 {len(gaps)}："))
            )
        error_text = self._truncate_to_cells(
            error or "無", max(8, width - self._cell_width("錯誤："))
        )
        lines = [
            f"狀態：{state_label}｜完成 {completed}｜命中 {matched}",
            f"接收 {received} B｜待讀 {backlog} B｜待處理：{'有' if pending else '無'}",
            f"最後讀取：{last_read_text}",
            *level_lines,
            f"缺口 {len(gaps)}：{gap_text}",
            f"錯誤：{error_text}",
        ]
        status.update("\n".join(lines))

    @classmethod
    def _format_level_counts(cls, level_counts, width: int) -> list[str]:
        if not level_counts:
            return ["Level：尚無"]

        values = dict(level_counts)
        names = list(_KNOWN_LEVELS)
        names.extend(sorted(name for name in values if name not in _KNOWN_LEVELS))
        tokens = [f"{name}={int(values.get(name, 0))}" for name in names]
        lines: list[str] = []
        current = "Level："
        for token in tokens:
            separator = "" if current == "Level：" else " "
            candidate = f"{current}{separator}{token}"
            if cls._cell_width(candidate) <= width or current == "Level：":
                current = candidate
                continue
            lines.append(current)
            current = f"  {token}"
        lines.append(current)
        return lines

    @staticmethod
    def _cell_width(value: str) -> int:
        return sum(
            2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
            for character in value
        )

    @classmethod
    def _truncate_to_cells(cls, value: str, max_width: int) -> str:
        if cls._cell_width(value) <= max_width:
            return value
        result: list[str] = []
        current_width = 0
        for character in value:
            character_width = cls._cell_width(character)
            if current_width + character_width + 1 > max_width:
                break
            result.append(character)
            current_width += character_width
        return "".join(result).rstrip() + "…"

    def _sync_controls(self, session: LiveSession | None) -> None:
        switch = self.query_one("#live-switch", Switch)
        rebuild = self.query_one("#live-rebuild", Button)
        summary = self.query_one("#live-summary", Button)
        stop = self.query_one("#live-stop", Button)
        previous = self.query_one("#live-previous", Button)
        next_page = self.query_one("#live-next", Button)
        if session is None:
            switch_value = False
            switch.disabled = self._busy
            rebuild.disabled = summary.disabled = stop.disabled = True
        else:
            state = session.state
            switch_value = state in {"tracking", "interrupted"}
            switch.disabled = self._busy or state in {"error", "stopped"}
            rebuild.disabled = self._busy
            summary.disabled = self._busy
            stop.disabled = self._busy or state == "stopped"
        if switch.value != switch_value:
            self._programmatic_switch_values.append(switch_value)
            switch.value = switch_value
        page_count = max(1, (self._matched + _PAGE_SIZE - 1) // _PAGE_SIZE)
        previous.disabled = self._page_index <= 0
        next_page.disabled = self._page_index >= page_count - 1

    def _render_page_indicator(self, page_count: int | None = None) -> None:
        if page_count is None:
            page_count = max(1, (self._matched + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self.query_one("#live-page", Static).update(
            f"{self._page_index + 1} / {page_count}"
        )

    def _render_new_count(self) -> None:
        text = "跟隨最新" if self._following else f"新增：{self._new_count} 筆"
        self.query_one("#live-new-events", Static).update(text)

    @staticmethod
    def _event_label(event: dict[str, Any]) -> str:
        sequence = event.get("sequence", "?")
        timestamp = str(event.get("timestamp", ""))
        level = str(event.get("level", ""))
        filename = str(event.get("filename", ""))
        generation = event.get("generation", 0)
        line_num = event.get("line_num", "?")
        message = " ".join(str(event.get("message", "")).split())
        if len(message) > 120:
            message = message[:117].rstrip() + "..."
        return (
            f"#{sequence} {timestamp} {level} · {filename} g{generation}:L{line_num}\n"
            f"  {message}"
        )

    @staticmethod
    def _consume_suppressed(values: list[Any], value: Any) -> bool:
        try:
            values.remove(value)
        except ValueError:
            return False
        return True
