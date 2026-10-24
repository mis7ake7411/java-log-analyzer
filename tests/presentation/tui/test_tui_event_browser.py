from __future__ import annotations

import asyncio

import pickle
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from tui_waits import wait_for_button_ready, wait_for_ui_state

from log_analyzer.application.analysis_service import AnalysisResult
from log_analyzer.application.source_preview import read_source_context
from log_analyzer.domain.parser import MatchedLogsStore
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.event_browser import EventBrowserScreen


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


def test_event_browser_selects_cross_file_coordinate_and_context(tmp_path: Path, monkeypatch) -> None:
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


def test_event_browser_pages_groups_and_occurrences(tmp_path: Path) -> None:
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


def test_event_browser_orders_occurrences_by_time_across_pages_and_selects_coordinate(
    tmp_path: Path,
) -> None:
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


def test_event_browser_resets_preview_scroll_for_another_occurrence(tmp_path: Path) -> None:
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
            preview_content.scroll_end(animate=False)
            await pilot.pause()
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


def test_event_browser_shows_nonfatal_encoding_notice_and_keeps_copy_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
