from __future__ import annotations

import asyncio
import os

import pickle
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Select, Static

from tui_waits import wait_for_button_ready, wait_for_ui_state, wait_for_layout_to_settle

from log_analyzer.application.analysis_service import AnalysisResult
from log_analyzer.application.source_preview import read_source_context
from log_analyzer.domain.parser import MatchedLogsStore
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.event_browser_text import highlight_literal
from log_analyzer.presentation.tui.screens.event_browser import EventBrowserScreen
from log_analyzer.presentation.tui.screens import event_browser as browser_module


class EventBrowserHarness(App[None]):
    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def __init__(self, result: AnalysisResult) -> None:
        self.result = result
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("dashboard")

    def on_mount(self) -> None:
        self.push_screen(EventBrowserScreen(self.result))


def _occurrence(
    filename: str,
    line_number: int,
    *,
    timestamp: str = "2026-10-20 12:00:00",
    thread: str = "main",
) -> dict[str, object]:
    return {
        "filename": filename,
        "line_number": line_number,
        "timestamp": timestamp,
        "thread": thread,
    }


def _group(index: int, occurrences: list[dict[str, object]]) -> dict[str, object]:
    return {
        "timestamp": "2026-10-20 12:00:00",
        "last_timestamp": "2026-10-20 12:00:00",
        "thread": "main",
        "level": "ERROR",
        "logger": f"com.example.Service{index}",
        "filename": occurrences[0]["filename"] if occurrences else "service.log",
        "occurrences": occurrences,
        "message": f"failure {index}",
        "message_body": f"failure {index}",
        "stacktrace": "",
        "stacktrace_lines": [],
        "count": len(occurrences),
        "line_numbers": [occurrence["line_number"] for occurrence in occurrences],
    }


def _snapshot(path: Path, *, stable: bool = True) -> dict[str, object]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "stable": stable}


def _result(
    tmp_path: Path,
    groups: list[dict[str, object]],
    source_states: dict[str, dict[str, object]] | None = None,
    input_path: Path | None = None,
    sort_by: str = "time",
) -> AnalysisResult:
    store_path = tmp_path / "matched-groups.pickle"
    with store_path.open("wb") as file_handle:
        for group in groups:
            pickle.dump(group, file_handle)
    store = MatchedLogsStore(
        path=str(store_path),
        length=len(groups),
        source_states=source_states or {},
    )
    occurrence_count = sum(len(group["occurrences"]) for group in groups)
    return AnalysisResult(
        input_path=str(input_path or tmp_path),
        output_path=str(tmp_path / "report.csv"),
        format_name="csv",
        keyword="failure",
        ignore_case=False,
        total_logs=occurrence_count,
        matched_groups=len(groups),
        matched_occurrences=occurrence_count,
        level_summary=[("ERROR", occurrence_count)],
        selected_levels=("ERROR",),
        sort_by=sort_by,
        counts=Counter({"ERROR": occurrence_count}),
        matched_logs=store,
        exported_files=[str(tmp_path / "report.csv")],
        source_states=source_states or {},
    )


class _BoundedOccurrenceList(list[dict[str, object]]):
    copied_completely = False

    def __iter__(self):
        self.copied_completely = True
        return super().__iter__()

    def __getitem__(self, index):
        if isinstance(index, slice):
            start = index.start or 0
            stop = len(self) if index.stop is None else index.stop
            if stop - start > 100:
                self.copied_completely = True
        return super().__getitem__(index)


def test_browser_query_filters_and_sorts_without_changing_analysis_result(tmp_path):
    """情境：事件查詢與排序應不修改原分析結果。"""
    groups = [
        _group(0, [_occurrence("app.log", 1)]),
        _group(1, [_occurrence("app.log", line) for line in (2, 3, 4)]),
        _group(2, [_occurrence("app.log", line, thread="worker-z") for line in (5, 6)]),
    ]
    groups[2]["last_timestamp"] = "2026-10-24 12:00:00"
    groups[2]["occurrences"][0]["timestamp"] = "2026-10-24 12:00:00"
    result = _result(tmp_path, groups)

    async def run_check():
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)) as pilot:
            browser = app.screen
            search = browser.query_one("#browser-query", Input)
            search.value = "worker-z"
            search.focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: browser._group_indices == [2], "thread query did not finish")
            assert len(browser.query_one("#event-options").options) == 1
            assert "Service2" in str(browser.query_one("#event-options").options[0].prompt)
            search.value = ""
            browser.query_one("#browser-order", Select).value = "count"
            search.focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: browser._group_indices == [1, 2, 0], "count sort did not finish")
            browser.query_one("#browser-order", Select).value = "recent"
            search.focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: browser._group_indices == [2, 0, 1], "recent sort did not finish")
            search.value = "no matching group"
            search.focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: browser._group_indices == [], "empty query did not finish")
            assert not browser.query_one("#event-options").options
            assert browser.query_one("#browser-next").disabled
            await wait_for_button_ready(app, pilot, "#browser-clear")
            browser.query_one("#browser-clear").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: browser._group_indices is None, "clear did not restore groups")
            assert len(browser.query_one("#event-options").options) == 3
            assert list(result.matched_logs) == groups
            assert result.matched_groups == 3
            assert result.matched_occurrences == 6

    try:
        asyncio.run(run_check())
    finally:
        result.matched_logs.release()


def test_browser_query_error_keeps_the_visible_page_and_reports_failure(tmp_path):
    """情境：查詢失敗應保留目前頁面並回報原因。"""
    groups = [_group(0, [_occurrence("app.log", 1)])]
    result = _result(tmp_path, groups)
    original_store = result.matched_logs

    class UnreadableStore(list):
        def __iter__(self):
            raise OSError("stored results unavailable")

        def __getitem__(self, index):
            if getattr(self, "unreadable", False):
                raise OSError("stored results unavailable")
            return super().__getitem__(index)

    store = UnreadableStore(groups)
    result.matched_logs = store

    async def run_check():
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)) as pilot:
            browser = app.screen
            options = browser.query_one("#event-options")
            before = str(options.options[0].prompt)
            store.unreadable = True
            search = browser.query_one("#browser-query", Input)
            search.value = "failure"
            search.focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: browser._query_task is not None and browser._query_task.done(), "failed query did not settle")
            assert browser._query_task.exception() is None
            assert "stored results unavailable" in browser.query_one("#browser-query-status", Static).render().plain
            assert str(options.options[0].prompt) == before
            assert not search.disabled

    try:
        asyncio.run(run_check())
    finally:
        original_store.release()


def test_closing_browser_cancels_its_active_query(tmp_path, monkeypatch):
    """情境：關閉瀏覽視窗應取消進行中的查詢。"""
    result = _result(tmp_path, [_group(0, [_occurrence("app.log", 1)])])

    async def run_check():
        entered, finished = asyncio.Event(), asyncio.Event()

        async def delayed_query(*_args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        monkeypatch.setattr(browser_module, "find_group_indices", delayed_query)
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)) as pilot:
            browser = app.screen
            search = browser.query_one("#browser-query", Input)
            search.value = "failure"
            search.focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, entered.is_set, "query did not start")
            browser.query_one("#close-event-browser").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, finished.is_set, "query was not cancelled on close")
            assert browser._query_task.cancelled()
            assert app.screen is not browser
            assert result.matched_groups == 1

    try:
        asyncio.run(run_check())
    finally:
        result.matched_logs.release()


def test_browser_back_preserves_group_and_occurrence_selection(tmp_path):
    """情境：返回上一層應保留群組與出現位置選擇。"""
    (tmp_path / "app.log").write_text("first\nsecond\nthird\n", encoding="utf-8")
    groups = [_group(index, [_occurrence("app.log", 1), _occurrence("app.log", 2)]) for index in range(4)]
    result = _result(tmp_path, groups, {"app.log": _snapshot(tmp_path / "app.log")})

    async def run_check():
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)) as pilot:
            browser = app.screen
            browser.page_size = 2
            browser._render_group_page()
            next_button = browser.query_one("#browser-next")
            await wait_for_button_ready(app, pilot, "#browser-next")
            next_button.focus()
            await pilot.press("enter")
            options = browser.query_one("#event-options")
            options.focus()
            await pilot.press("down", "enter")
            options.focus()
            await pilot.press("down", "enter")
            await wait_for_ui_state(pilot, lambda: browser._view == "preview", "preview did not open")
            for expected in ("occurrences", "groups"):
                await wait_for_button_ready(app, pilot, "#browser-back")
                browser.query_one("#browser-back").focus()
                await pilot.press("enter")
                await wait_for_ui_state(pilot, lambda: browser._view == expected, "back did not restore list")
                assert options.highlighted == 1
            assert browser._group_start == 2
            assert "Service3" in str(options.options[1].prompt)

    try:
        asyncio.run(run_check())
    finally:
        result.matched_logs.release()


def test_browser_query_controls_and_actions_fit_short_narrow_and_wide_screens(tmp_path):
    """情境：查詢控制與操作在短窄及寬視窗都應完整可見。"""
    result = _result(tmp_path, [_group(0, [_occurrence("app.log", 1)])])

    async def run_check():
        for size in ((64, 18), (72, 28), (160, 40)):
            app = EventBrowserHarness(result)
            async with app.run_test(size=size) as pilot:
                browser = app.screen
                search = browser.query_one("#browser-query", Input)
                search.value = "failure"
                search.focus()
                await pilot.press("enter")
                await wait_for_ui_state(pilot, lambda: browser._group_indices == [0], "search did not render")
                ids = ("browser-query", "browser-order", "browser-apply", "browser-clear", "browser-previous", "browser-next", "close-event-browser")
                for widget_id in ids:
                    widget = browser.query_one(f"#{widget_id}")
                    assert widget.region.bottom <= browser.region.bottom, (size, widget_id, widget.region)
                    assert widget.region.right <= browser.region.right, (size, widget_id, widget.region)
                    assert widget.is_on_screen, (size, widget_id)
                capture = os.environ.get("TUI_CAPTURE_DIR")
                if capture:
                    Path(capture).mkdir(parents=True, exist_ok=True)
                    app.save_screenshot(filename=f"event-query-{size[0]}x{size[1]}.svg", path=capture)

    try:
        asyncio.run(run_check())
    finally:
        result.matched_logs.release()


def test_event_browser_selects_cross_file_coordinate_and_context(tmp_path: Path, monkeypatch) -> None:
    """情境：事件瀏覽應選到跨檔案正確座標及上下文。"""
    first_path = tmp_path / "first.log"
    second_path = tmp_path / "second.log"
    first_path.write_text("first file match\n", encoding="utf-8")
    second_path.write_text("\n".join(f"line-{number}" for number in range(1, 10)), encoding="utf-8")
    result = _result(
        tmp_path,
        [_group(1, [_occurrence("first.log", 1), _occurrence("second.log", 7)])],
        {"first.log": _snapshot(first_path), "second.log": _snapshot(second_path)},
    )
    copied: list[str] = []

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            browser = app.screen
            await pilot.press("enter")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            expected = f"{second_path.resolve()}:7"
            assert str(browser.query_one("#source-path", Static).render()) == expected
            assert "line-7" in str(browser.query_one("#source-context", Static).render())

            await pilot.click("#copy-coordinate")

            assert copied == [expected]
            assert str(browser.query_one("#copy-feedback", Static).render()) == "已複製路徑:行號至剪貼簿。"

    asyncio.run(run_check())


def test_browser_query_highlight_overrides_analysis_keyword(tmp_path):
    """情境：已套用的事件查詢應取代原分析關鍵字的醒目標示。"""
    group = _group(0, [_occurrence("app.log", 1)])
    group["message_body"] = "analysis Needle"
    result = _result(tmp_path, [group])
    result.keyword = "analysis"
    result.ignore_case = False

    async def run_check():
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)) as pilot:
            browser = app.screen
            search = browser.query_one("#browser-query", Input)
            search.value = "needle"
            search.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._group_indices == [0]
                and browser._active_browser_query == "needle",
                "browser query did not finish applying",
            )

            prompt = browser.query_one("#event-options").options[0].prompt
            assert isinstance(prompt, Text)

            def highlighted(term: str) -> bool:
                start = prompt.plain.index(term)
                end = start + len(term)
                return any(
                    span.start <= start
                    and span.end >= end
                    and "on yellow" in str(span.style)
                    for span in prompt.spans
                )

            assert highlighted("Needle")
            assert not highlighted("analysis")

    asyncio.run(run_check())


@pytest.mark.parametrize(
    "has_analysis_request",
    [pytest.param(True, id="analysis-request"), pytest.param(False, id="legacy-result")],
)
def test_event_browser_does_not_highlight_unset_analysis_keyword_placeholder(
    tmp_path: Path,
    has_analysis_request: bool,
) -> None:
    """情境：沒有分析關鍵字時不應把摘要佔位文字標成搜尋結果。"""
    group = _group(0, [_occurrence("app.log", 1)])
    group["message_body"] = "未設定 is a display placeholder"
    result = _result(tmp_path, [group])
    result.keyword = "未設定"
    result.analysis_request = (
        SimpleNamespace(keyword=None, ignore_case=False)
        if has_analysis_request
        else None
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)):
            prompt = app.screen.query_one("#event-options").options[0].prompt
            assert isinstance(prompt, Text)
            start = prompt.plain.index("未設定")
            end = start + len("未設定")
            assert not any(
                span.start <= start
                and span.end >= end
                and "on yellow" in str(span.style)
                for span in prompt.spans
            )

    asyncio.run(run_check())


def test_literal_highlight_maps_unicode_casefold_expansion_to_original_offsets() -> None:
    """情境：Unicode casefold 展開時標示仍須落在原字元位置。"""
    source = Text("Prefix Straße suffix")
    highlighted = highlight_literal(source, "STRASSE", ignore_case=True)
    start = highlighted.plain.index("Straße")
    end = start + len("Straße")

    assert any(
        span.start == start
        and span.end == end
        and "on yellow" in str(span.style)
        for span in highlighted.spans
    )


def test_event_browser_pages_groups_and_occurrences(tmp_path: Path) -> None:
    """情境：群組與出現位置應支援分頁。"""
    occurrences = [_occurrence("many.log", number) for number in range(1, 206)]
    groups = [_group(index, [_occurrence("many.log", index + 1)]) for index in range(100)]
    groups.append(_group(100, occurrences))
    result = _result(tmp_path, groups)

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            browser = app.screen
            options = browser.query_one("#event-options")
            assert len(options.options) == 100
            next_button = browser.query_one("#browser-next")
            assert next_button.is_on_screen, (
                f"app={app.size}, screen={browser.size}, card={browser.query_one('#event-browser-card').region}, "
                f"button={next_button.region}"
            )
            next_button.focus()
            await wait_for_button_ready(app, pilot, "#browser-next")
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: len(options.options) == 1, "group page did not change")
            assert len(options.options) == 1
            assert "Service100" in str(options.options[0].prompt)

            options.focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: len(options.options) == 100, "occurrence page did not open")
            assert len(options.options) == 100

            next_button.focus()
            await wait_for_button_ready(app, pilot, "#browser-next")
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: str(options.options[0].prompt).startswith("many.log:101"),
                "second occurrence page did not open",
            )
            assert not browser.query_one("#browser-next").disabled
            assert len(options.options) == 100
            next_button.focus()
            await wait_for_button_ready(app, pilot, "#browser-next")
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: len(options.options) == 5, "last occurrence page did not open")
            assert len(options.options) == 5
            assert str(options.options[0].prompt).startswith("many.log:201")

    asyncio.run(run_check())


def test_event_browser_does_not_copy_the_complete_occurrence_list(tmp_path: Path) -> None:
    """情境：事件瀏覽不應複製全部出現位置清單。"""
    group = _group(1, [_occurrence("many.log", 1)])
    occurrences = _BoundedOccurrenceList(
        _occurrence("many.log", number) for number in range(1, 206)
    )
    group["occurrences"] = occurrences
    result = SimpleNamespace(
        input_path=str(tmp_path),
        matched_logs=[group],
        source_states={},
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert len(app.screen.query_one("#event-options").options) == 100
            assert not occurrences.copied_completely

    asyncio.run(run_check())


def test_event_browser_labels_are_traditional_chinese(tmp_path: Path) -> None:
    """情境：事件瀏覽標籤應使用繁體中文。"""
    result = _result(tmp_path, [_group(1, [_occurrence("sample.log", 1)])])

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            browser = app.screen

            assert str(browser.query_one("#browser-title", Static).render()) == "符合事件群組"
            assert str(browser.query_one("#browser-sort-hint", Static).render()) == (
                "排序：時間由早到晚"
            )
            assert str(browser.query_one("#browser-previous").label) == "上一頁"
            assert str(browser.query_one("#browser-next").label) == "下一頁"
            assert str(browser.query_one("#copy-coordinate").label) == "複製路徑:行號"
            assert str(browser.query_one("#close-event-browser").label) == "關閉"

            await pilot.press("enter")
            await pilot.pause()
            assert str(browser.query_one("#browser-title", Static).render()) == "事件出現位置"

            await pilot.press("enter")
            await pilot.pause()
            assert str(browser.query_one("#browser-title", Static).render()) == "來源內容預覽"
            assert str(browser.query_one("#browser-back").label) == "返回事件出現位置"

    asyncio.run(run_check())


def test_event_browser_group_labels_separate_fields_and_show_result_order(tmp_path: Path) -> None:
    """情境：群組標籤應分開欄位並顯示結果順序。"""
    group = _group(1, [_occurrence("sample.log", 7)])
    group.update(
        timestamp="2026-10-20 12:00:00",
        last_timestamp="2026-10-20 12:30:00",
        message_body="first line\n[bold red]literal markup[/bold red]",
    )
    result = _result(tmp_path, [group], sort_by="level")

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            browser = app.screen
            option = browser.query_one("#event-options").options[0]
            prompt = option.prompt

            assert isinstance(prompt, Text)
            assert prompt.plain == (
                "層級 / 次數：ERROR / 1 次\n"
                "時間：2026-10-20 12:00:00 → 2026-10-20 12:30:00\n"
                "記錄器：com.example.Service1\n"
                "訊息：\n"
                "first line\n"
                "[bold red]literal markup[/bold red]\n"
                "來源：sample.log:7"
            )
            sort_hints = browser.query("#browser-sort-hint")
            assert len(sort_hints) == 1
            assert str(sort_hints.first().render()) == "排序：層級順序，再依時間由早到晚"

            await pilot.press("enter")
            await pilot.pause()
            assert str(browser.query_one("#browser-sort-hint", Static).render()) == (
                "排序：本群組內依時間由早到晚"
            )

    asyncio.run(run_check())


@pytest.mark.parametrize(
    "message_body, remove_body",
    [
        pytest.param("", False, id="empty"),
        pytest.param(None, False, id="none"),
        pytest.param(17, False, id="non-string"),
        pytest.param(None, True, id="missing"),
    ],
)
def test_event_browser_message_renderers_fall_back_to_header_message(
    message_body: object, remove_body: bool
) -> None:
    """情境：本文空白、缺少或非字串時應退回首行訊息。"""
    occurrence = _occurrence("failure.log", 7)
    group = _group(1, [occurrence])
    group["message"] = "Failure beta"
    if remove_body:
        group.pop("message_body")
    else:
        group["message_body"] = message_body

    rendered = (
        EventBrowserScreen._group_label(group).plain,
        EventBrowserScreen._retained_details_text(group, occurrence).plain,
    )

    for text in rendered:
        assert "Failure beta" in text
        assert "（無保留訊息）" not in text


def test_event_browser_message_renderers_prefer_body_and_keep_empty_placeholder() -> None:
    """情境：非空本文應優先使用且完全空白時保留佔位提示。"""
    occurrence = _occurrence("failure.log", 7)
    group = _group(1, [occurrence])
    group.update(message="Failure beta", message_body="Retained body")

    for text in (
        EventBrowserScreen._group_label(group).plain,
        EventBrowserScreen._retained_details_text(group, occurrence).plain,
    ):
        assert "Retained body" in text
        assert "Failure beta" not in text

    group.update(message="", message_body="")
    for text in (
        EventBrowserScreen._group_label(group).plain,
        EventBrowserScreen._retained_details_text(group, occurrence).plain,
    ):
        assert "（無保留訊息）" in text


def test_event_browser_orders_occurrences_by_time_across_pages_and_selects_coordinate(
    tmp_path: Path,
) -> None:
    """情境：出現位置應跨頁依時間排序並定位正確來源。"""
    page_two_path = tmp_path / "late-095.log"
    page_two_path.write_text("late match\n", encoding="utf-8")
    source_states = {"late-095.log": _snapshot(page_two_path)}
    occurrences = [
        _occurrence("later.log", 3, timestamp="2026-10-20 12:02:00"),
        _occurrence("b.log", 9, timestamp="2026-10-20 12:01:00"),
        _occurrence("a.log", 5, timestamp="2026-10-20 12:01:00", thread="first"),
        _occurrence("a.log", 1, timestamp="2026-10-20 12:01:00"),
        _occurrence("a.log", 5, timestamp="2026-10-20 12:01:00", thread="second"),
        _occurrence("early.log", 1, timestamp="2026-10-20 12:00:00"),
        *[
            _occurrence(
                f"late-{index:03}.log",
                1,
                timestamp="2026-10-20 12:05:00",
            )
            for index in range(100)
        ],
    ]
    result = _result(
        tmp_path,
        [_group(1, occurrences)],
        source_states=source_states,
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            browser = app.screen
            options = browser.query_one("#event-options")
            await pilot.press("enter")
            await pilot.pause()

            labels = [
                option.prompt.plain if isinstance(option.prompt, Text) else str(option.prompt)
                for option in options.options
            ]
            assert labels[:6] == [
                "early.log:1｜時間 2026-10-20 12:00:00｜執行緒 main",
                "a.log:1｜時間 2026-10-20 12:01:00｜執行緒 main",
                "a.log:5｜時間 2026-10-20 12:01:00｜執行緒 first",
                "a.log:5｜時間 2026-10-20 12:01:00｜執行緒 second",
                "b.log:9｜時間 2026-10-20 12:01:00｜執行緒 main",
                "later.log:3｜時間 2026-10-20 12:02:00｜執行緒 main",
            ]
            assert len(options.options) == 100

            next_button = browser.query_one("#browser-next")
            next_button.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert len(options.options) == 6
            second_page_labels = [
                option.prompt.plain if isinstance(option.prompt, Text) else str(option.prompt)
                for option in options.options
            ]
            assert second_page_labels[0].startswith("late-094.log:1")
            assert second_page_labels[1].startswith("late-095.log:1")

            options.focus()
            await pilot.press("down", "enter")
            await pilot.pause()
            assert str(browser.query_one("#source-path", Static).render()) == (
                f"{page_two_path.resolve()}:1"
            )

    asyncio.run(run_check())


def test_event_browser_close_does_not_clear_result_occurrences(tmp_path: Path) -> None:
    """情境：關閉事件瀏覽不得清空結果中的出現位置。"""
    occurrences = [
        _occurrence("same.log", 3),
        _occurrence("same.log", 1),
        _occurrence("same.log", 2),
    ]
    original = [dict(occurrence) for occurrence in occurrences]
    group = _group(1, occurrences)
    result = SimpleNamespace(
        input_path=str(tmp_path),
        matched_logs=[group],
        source_states={},
        sort_by="time",
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#close-event-browser")
            await pilot.pause()

            assert occurrences == original

    asyncio.run(run_check())


def test_event_browser_shows_retained_details_separately_from_raw_context(tmp_path: Path) -> None:
    """情境：保留的解析內容應與來源原文上下文分區顯示。"""
    source_path = tmp_path / "details.log"
    source_path.write_text(
        "\n".join(f"physical line {number}" for number in range(1, 11)),
        encoding="utf-8",
    )
    group = _group(
        1,
        [
            _occurrence(
                "details.log",
                7,
                timestamp="2026-10-20 12:15:00",
                thread="worker-7",
            )
        ],
    )
    group["timestamp"] = "2026-10-20 12:00:00"
    group["last_timestamp"] = "2026-10-20 12:30:00"
    group["message_body"] = "retained first line\nretained second line\n[red]literal[/red]"
    group["stacktrace"] = "Traceback (most recent call last):\n  at Example.run()"
    result = _result(
        tmp_path,
        [group],
        {"details.log": _snapshot(source_path)},
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter", "enter")
            await pilot.pause()
            browser = app.screen

            retained_widgets = browser.query("#retained-details")
            assert len(retained_widgets) == 1
            assert retained_widgets.first().display
            retained = retained_widgets.first().render()
            retained_text = retained.plain if isinstance(retained, Text) else str(retained)
            assert retained_text.startswith(
                "選取的出現位置\n時間：2026-10-20 12:15:00\n"
                "執行緒：worker-7\n座標：details.log:7\n"
            )
            assert "事件群組資訊" in retained_text
            assert "時間範圍：2026-10-20 12:00:00 → 2026-10-20 12:30:00" in retained_text
            representative_heading = "分析保留的群組代表（可能不是目前選取位置的原始內容）"
            assert retained_text.index("事件群組資訊") < retained_text.index(representative_heading)
            assert "分析保留的群組代表" in retained_text
            assert "retained first line\nretained second line\n[red]literal[/red]" in retained_text
            assert "Traceback (most recent call last):\n  at Example.run()" in retained_text
            assert retained_text.index(representative_heading) < retained_text.index("訊息：")

            context = str(browser.query_one("#source-context", Static).render())
            assert context.startswith("已核對的原文前後文\n")
            assert "physical line 7" in context
            assert "retained first line" not in context
            assert "Example.run()" not in context

    asyncio.run(run_check())


@pytest.mark.parametrize(
    ("ignore_case", "expected_highlight"),
    [pytest.param(True, True, id="ignore-case"), pytest.param(False, False, id="case-sensitive")],
)
def test_event_browser_highlights_analysis_keyword_as_case_sensitive_literal(
    tmp_path: Path,
    ignore_case: bool,
    expected_highlight: bool,
) -> None:
    """情境：分析關鍵字應依大小寫設定以 literal 樣式標示。"""
    source_path = tmp_path / "highlight.log"
    source_path.write_text(
        "Failure [tag] source [red]literal[/red]\n",
        encoding="utf-8",
    )
    group = _group(1, [_occurrence("highlight.log", 1)])
    group["message_body"] = "Failure [tag] retained [bold red]literal[/bold red]"
    result = _result(
        tmp_path,
        [group],
        {"highlight.log": _snapshot(source_path)},
    )
    result.keyword = "未設定"
    result.ignore_case = not ignore_case
    result.analysis_request = SimpleNamespace(
        keyword="[TAG]",
        ignore_case=ignore_case,
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)) as pilot:
            browser = app.screen
            await pilot.press("enter", "enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "preview",
                "event source preview did not open",
            )

            for selector, unchanged_literal in (
                ("#retained-details", "[bold red]literal[/bold red]"),
                ("#source-context", "[red]literal[/red]"),
            ):
                rendered = browser.query_one(selector, Static).render()
                plain = getattr(rendered, "plain", str(rendered))
                assert unchanged_literal in plain
                start = plain.index("[tag]")
                end = start + len("[tag]")
                is_highlighted = any(
                    span.start <= start
                    and span.end >= end
                    and span.style.background is not None
                    for span in getattr(rendered, "spans", ())
                )
                assert is_highlighted is expected_highlight, selector

    asyncio.run(run_check())


def test_event_browser_collapses_long_stacktrace_at_eight_lines(tmp_path: Path) -> None:
    """情境：超過八行的保留堆疊應先顯示八行並提供展開操作。"""
    source_path = tmp_path / "stacktrace.log"
    source_path.write_text("match\n", encoding="utf-8")
    group = _group(1, [_occurrence("stacktrace.log", 1)])
    group["stacktrace"] = "\n".join(f"frame-{index}" for index in range(1, 11))
    result = _result(
        tmp_path,
        [group],
        {"stacktrace.log": _snapshot(source_path)},
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 38)) as pilot:
            browser = app.screen
            await pilot.press("enter", "enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "preview",
                "event source preview did not open",
            )

            retained = browser.query_one("#retained-details", Static).render()
            assert "frame-1" in retained.plain
            assert "frame-8" in retained.plain
            assert "frame-9" not in retained.plain
            toggle = browser.query("#stacktrace-toggle")
            assert len(toggle) == 1
            assert toggle.first().display

            toggle.first().focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._stacktrace_expanded,
                "stacktrace did not expand",
            )
            assert toggle.first().has_focus
            expanded = browser.query_one("#retained-details", Static).render()
            assert "frame-9" in expanded.plain
            assert "frame-10" in expanded.plain
            assert str(toggle.first().label) == "收合堆疊"

            await wait_for_layout_to_settle(
                pilot,
                (
                    toggle.first(),
                    browser.query_one("#browser-preview-content", VerticalScroll),
                ),
            )
            toggle.first().focus()
            # Layout completion does not clear Button's timed -active effect.
            await wait_for_ui_state(
                pilot,
                lambda: toggle.first().has_focus and not toggle.first().has_class("-active"),
                "stacktrace toggle is not ready for another key activation",
            )
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: not browser._stacktrace_expanded,
                "stacktrace did not collapse",
            )
            collapsed = browser.query_one("#retained-details", Static).render()
            assert "frame-9" not in collapsed.plain

    asyncio.run(run_check())


@pytest.mark.parametrize(
    "stacktrace",
    [pytest.param("", id="empty"), pytest.param("frame-a\nframe-b", id="short")],
)
def test_event_browser_does_not_offer_stacktrace_toggle_for_short_or_empty_text(
    tmp_path: Path,
    stacktrace: str,
) -> None:
    """情境：短堆疊與空堆疊不需要展開或收合控制。"""
    source_path = tmp_path / "short-stacktrace.log"
    source_path.write_text("match\n", encoding="utf-8")
    group = _group(1, [_occurrence("short-stacktrace.log", 1)])
    group["stacktrace"] = stacktrace
    result = _result(
        tmp_path,
        [group],
        {"short-stacktrace.log": _snapshot(source_path)},
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(80, 24)) as pilot:
            browser = app.screen
            await pilot.press("enter", "enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "preview",
                "event source preview did not open",
            )
            assert not browser.query_one("#stacktrace-toggle").display

    asyncio.run(run_check())


def test_event_browser_preview_actions_are_keyboard_reachable_without_overflow(
    tmp_path: Path,
) -> None:
    """情境：預覽操作在窄短與寬視窗都可用 Tab 到達且不水平溢出。"""
    source_path = tmp_path / "keyboard-preview.log"
    source_path.write_text("match\n", encoding="utf-8")
    group = _group(1, [_occurrence("keyboard-preview.log", 1)])
    group["message_body"] = "retained representative message"
    group["stacktrace"] = "\n".join(f"frame-{index}" for index in range(1, 11))
    result = _result(
        tmp_path,
        [group],
        {"keyboard-preview.log": _snapshot(source_path)},
    )
    action_ids = (
        "copy-retained-message",
        "copy-full-stacktrace",
        "stacktrace-toggle",
        "browser-back",
        "copy-coordinate",
        "close-event-browser",
    )

    async def run_check() -> None:
        for size in ((64, 18), (80, 24), (160, 44)):
            app = EventBrowserHarness(result)
            async with app.run_test(size=size) as pilot:
                browser = app.screen
                await pilot.press("enter", "enter")
                await wait_for_ui_state(
                    pilot,
                    lambda: browser._view == "preview",
                    "event source preview did not open",
                )
                preview_content = browser.query_one(
                    "#browser-preview-content", VerticalScroll
                )
                retained_details = browser.query_one("#retained-details", Static)
                assert retained_details.display
                assert "retained representative" in retained_details.render().plain
                assert "frame-8" in retained_details.render().plain
                await wait_for_layout_to_settle(
                    pilot,
                    (preview_content, retained_details),
                )
                preview_content.scroll_to_widget(
                    retained_details,
                    animate=False,
                    center=True,
                )
                await wait_for_ui_state(
                    pilot,
                    lambda: retained_details.is_on_screen,
                    "retained representative was not scrollable into view",
                )
                if size[1] <= 24:
                    await wait_for_ui_state(
                        pilot,
                        lambda: preview_content.scroll_y > 0,
                        "preview did not scroll to the retained representative",
                    )
                capture = os.environ.get("TUI_CAPTURE_DIR")
                if capture and size in ((80, 24), (160, 44)):
                    Path(capture).mkdir(parents=True, exist_ok=True)
                    app.save_screenshot(
                        filename=f"event-preview-{size[0]}x{size[1]}.svg",
                        path=capture,
                    )

                actions = {
                    action_id: browser.query_one(f"#{action_id}")
                    for action_id in action_ids
                }
                assert all(action.display and not action.disabled for action in actions.values())
                assert all(
                    action.region.x + action.region.width <= app.size.width
                    for action in actions.values()
                ), (size, {key: value.region for key, value in actions.items()})

                actions["close-event-browser"].focus()
                reached: set[str] = set()
                for _ in range(len(action_ids) + 1):
                    await pilot.press("tab")
                    focused_id = next(
                        (
                            action_id
                            for action_id, action in actions.items()
                            if action.has_focus
                        ),
                        None,
                    )
                    if focused_id is not None:
                        reached.add(focused_id)
                        assert actions[focused_id].is_on_screen, (size, focused_id)
                assert reached == set(action_ids), (size, reached)

    asyncio.run(run_check())


def test_event_browser_resets_preview_scroll_for_another_occurrence(tmp_path: Path) -> None:
    """情境：改選另一出現位置時預覽捲動應回頂端。"""
    first_path = tmp_path / "first-scroll.log"
    second_path = tmp_path / "second-scroll.log"
    first_path.write_text("first physical match\n", encoding="utf-8")
    second_path.write_text("second physical match\n", encoding="utf-8")
    occurrences = [
        _occurrence(
            "first-scroll.log",
            1,
            timestamp="2026-10-20 12:00:00",
            thread="first-thread",
        ),
        _occurrence(
            "second-scroll.log",
            1,
            timestamp="2026-10-20 12:01:00",
            thread="second-thread",
        ),
    ]
    group = _group(1, occurrences)
    group["message_body"] = "\n".join(f"retained line {index}" for index in range(30))
    group["stacktrace"] = "\n".join(f"  at Example.frame{index}()" for index in range(20))
    result = _result(
        tmp_path,
        [group],
        {
            "first-scroll.log": _snapshot(first_path),
            "second-scroll.log": _snapshot(second_path),
        },
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            browser = app.screen
            await pilot.press("enter", "enter")
            await pilot.pause()

            preview_content = browser.query_one("#browser-preview-content", VerticalScroll)
            await wait_for_layout_to_settle(pilot, (preview_content,))
            preview_content.scroll_end(animate=False)
            await wait_for_ui_state(
                pilot, lambda: preview_content.scroll_y > 0, "source preview scroll did not move"
            )
            assert preview_content.scroll_y > 0

            await pilot.click("#browser-back")
            await pilot.pause()
            browser.query_one("#event-options").focus()
            await pilot.press("down", "enter")
            await pilot.pause()

            retained = browser.query_one("#retained-details", Static).render()
            retained_text = retained.plain if isinstance(retained, Text) else str(retained)
            assert "時間：2026-10-20 12:01:00" in retained_text
            assert "執行緒：second-thread" in retained_text
            assert "座標：second-scroll.log:1" in retained_text
            assert preview_content.scroll_y == 0

    asyncio.run(run_check())


def test_event_browser_keeps_preview_actions_reachable_on_narrow_and_wide_screens(
    tmp_path: Path,
) -> None:
    """情境：寬窄視窗下預覽操作都應可到達。"""
    source_path = tmp_path / "reachable.log"
    source_path.write_text("match\n", encoding="utf-8")
    result = _result(
        tmp_path,
        [_group(1, [_occurrence("reachable.log", 1)])],
        {"reachable.log": _snapshot(source_path)},
    )

    async def run_check() -> None:
        for size in ((64, 18), (160, 40)):
            app = EventBrowserHarness(result)
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                await pilot.press("enter", "enter")
                await pilot.pause()
                browser = app.screen
                action_ids = (
                    "browser-back",
                    "copy-coordinate",
                    "close-event-browser",
                )
                action_widgets = [
                    (button_id, browser.query_one(f"#{button_id}"))
                    for button_id in action_ids
                ]

                assert all(widget.is_on_screen for _, widget in action_widgets), (
                    f"size={size}, actions="
                    f"{[(button_id, widget.region) for button_id, widget in action_widgets]}"
                )
                assert all(
                    widget.region.y + widget.region.height <= app.size.height
                    for _, widget in action_widgets
                )
                assert not browser.query_one("#browser-previous").display
                assert not browser.query_one("#browser-next").display

    asyncio.run(run_check())


def test_event_browser_shows_stale_warning_without_copy(tmp_path: Path) -> None:
    """情境：來源過期應顯示警告並停用複製。"""
    source_path = tmp_path / "changed.log"
    source_path.write_text("line-1", encoding="utf-8")
    group = _group(1, [_occurrence("changed.log", 1)])
    group["message_body"] = "retained after source became stale"
    group["stacktrace"] = "at Example.failure()"
    result = _result(
        tmp_path,
        [group],
        {"changed.log": _snapshot(source_path, stable=False)},
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.press("enter")

            browser = app.screen
            warning = str(browser.query_one("#source-warning", Static).render())
            assert "來源已變更" in warning
            assert browser.query_one("#copy-coordinate").disabled is True
            retained_widgets = browser.query("#retained-details")
            assert len(retained_widgets) == 1
            retained = retained_widgets.first().render()
            retained_text = retained.plain if isinstance(retained, Text) else str(retained)
            assert "retained after source became stale" in retained_text
            assert "at Example.failure()" in retained_text
            source_context = browser.query_one("#source-context", Static)
            assert not source_context.display
            assert str(source_context.render()) == ""

    asyncio.run(run_check())


def test_event_browser_copies_retained_representative_and_complete_stacktrace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """情境：來源過期時仍可複製群組代表訊息與完整保留堆疊。"""
    source_path = tmp_path / "stale-retained.log"
    source_path.write_text("changed source\n", encoding="utf-8")
    message = "retained representative message"
    stacktrace = "\n".join(f"frame-{index}" for index in range(1, 11))
    group = _group(1, [_occurrence("stale-retained.log", 1)])
    group["message_body"] = message
    group["stacktrace"] = stacktrace
    result = _result(
        tmp_path,
        [group],
        {"stale-retained.log": _snapshot(source_path, stable=False)},
    )
    copied: list[str] = []

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(64, 18)) as pilot:
            browser = app.screen
            await pilot.press("enter", "enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "preview",
                "event source preview did not open",
            )

            assert browser.query_one("#source-warning", Static).display
            assert not browser.query_one("#source-context", Static).display
            assert browser.query_one("#copy-coordinate").disabled
            message_buttons = browser.query("#copy-retained-message")
            stack_buttons = browser.query("#copy-full-stacktrace")
            assert len(message_buttons) == 1
            assert len(stack_buttons) == 1

            message_button = message_buttons.first()
            message_button.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: copied == [message],
                "retained message was not copied",
            )
            stack_button = stack_buttons.first()
            stack_button.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: len(copied) == 2,
                "full retained stacktrace was not copied",
            )

            assert copied == [message, stacktrace]
            feedback = str(browser.query_one("#copy-feedback", Static).render())
            assert "不代表已核對原文" in feedback

    asyncio.run(run_check())


def test_event_browser_shows_nonfatal_encoding_notice_and_keeps_copy_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """情境：非致命編碼提示不應停用原文複製。"""
    source_path = tmp_path / "invalid-utf8.log"
    source_path.write_bytes(b"matched \x80 line\n")
    result = _result(
        tmp_path,
        [_group(1, [_occurrence("invalid-utf8.log", 1)])],
        {"invalid-utf8.log": _snapshot(source_path)},
    )
    copied: list[str] = []

    async def run_check() -> None:
        app = EventBrowserHarness(result)
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.press("enter")

            browser = app.screen
            notice = browser.query_one("#source-encoding-notice", Static)
            assert notice.display
            assert "UTF-8" in str(notice.render())
            assert not browser.query_one("#source-warning", Static).display
            context = browser.query_one("#source-context", Static)
            assert "matched � line" in str(context.render())
            assert browser.query_one("#copy-coordinate").disabled is False

            await pilot.click("#copy-coordinate")
            assert copied == [f"{source_path.resolve()}:1"]

    asyncio.run(run_check())


def test_event_browser_keeps_full_path_visible_when_clipboard_fails(tmp_path: Path, monkeypatch) -> None:
    """情境：剪貼簿失敗時仍應保留完整可讀路徑。"""
    source_path = tmp_path / "manual-copy.log"
    source_path.write_text("line-1\n", encoding="utf-8")
    result = _result(
        tmp_path,
        [_group(1, [_occurrence("manual-copy.log", 1)])],
        {"manual-copy.log": _snapshot(source_path)},
    )

    async def run_check() -> None:
        app = EventBrowserHarness(result)

        def fail_copy(_text: str) -> None:
            raise RuntimeError("clipboard unavailable")

        monkeypatch.setattr(app, "copy_to_clipboard", fail_copy)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.click("#copy-coordinate")

            expected = f"{source_path.resolve()}:1"
            browser = app.screen
            assert str(browser.query_one("#source-path", Static).render()) == expected
            feedback = str(browser.query_one("#copy-feedback", Static).render())
            assert "剪貼簿複製失敗，請手動複製：" in feedback
            assert expected in feedback

    asyncio.run(run_check())


def test_event_browser_owns_and_clears_private_source_indexes(tmp_path: Path) -> None:
    """情境：瀏覽視窗應自行擁有並清理來源定位索引。"""
    source_path = tmp_path / "lifecycle.log"
    source_path.write_text(
        "".join(f"來源行 {line_number}\n" for line_number in range(1, 401)),
        encoding="utf-8",
    )
    result = SimpleNamespace(input_path=str(tmp_path), matched_logs=[], source_states={})
    first_browser = EventBrowserScreen(result)
    second_browser = EventBrowserScreen(result)
    first_reader = first_browser._source_context_reader
    second_reader = second_browser._source_context_reader

    preview = read_source_context(
        str(tmp_path),
        _occurrence("lifecycle.log", 300),
        _snapshot(source_path),
        reader=first_reader,
    )

    assert preview.warning is None
    assert first_reader is not second_reader
    assert source_path.resolve() in first_reader._entries
    assert second_reader._entries == {}

    first_browser.on_unmount()

    assert first_reader._entries == {}
    assert second_reader._entries == {}
