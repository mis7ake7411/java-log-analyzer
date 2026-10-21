from __future__ import annotations

import asyncio
import pickle
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from textual.app import App, ComposeResult
from textual.widgets import Static

from log_analyzer.application.analysis_service import AnalysisResult
from log_analyzer.domain.parser import MatchedLogsStore
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui_event_browser import EventBrowserScreen


class EventBrowserHarness(App[None]):
    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def __init__(self, result: AnalysisResult) -> None:
        self.result = result
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("dashboard")

    def on_mount(self) -> None:
        self.push_screen(EventBrowserScreen(self.result))


def _occurrence(filename: str, line_number: int) -> dict[str, object]:
    return {
        "filename": filename,
        "line_number": line_number,
        "timestamp": "2026-10-20 12:00:00",
        "thread": "main",
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
        sort_by="time",
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
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert len(options.options) == 1
            assert "Service100" in str(options.options[0].prompt)

            options.focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert len(options.options) == 100

            next_button.focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert not browser.query_one("#browser-next").disabled
            assert len(options.options) == 100
            next_button.focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
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


def test_event_browser_shows_stale_warning_without_copy(tmp_path: Path) -> None:
    source_path = tmp_path / "changed.log"
    source_path.write_text("line-1", encoding="utf-8")
    result = _result(
        tmp_path,
        [_group(1, [_occurrence("changed.log", 1)])],
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
