"""相似事件畫面的操作、回返與窄寬版行為驗證。"""

import asyncio
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Button, Input, OptionList, Static

from tui_waits import wait_for_button_ready, wait_for_layout_to_settle, wait_for_ui_state

from log_analyzer.application.analysis.service import AnalysisResult
from log_analyzer.application.events.similarity import SimilarGroup
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.screens.event_browser import EventBrowserScreen
from log_analyzer.presentation.tui.screens.event_similarity import EventSimilarityScreen
from log_analyzer.presentation.tui.screens import event_similarity as similarity_screen


@dataclass(frozen=True, slots=True)
class _GroupDetails:
    level: str
    count: int
    filename: str
    line_number: int


def _group(
    message: str,
    message_body: str,
    details: _GroupDetails,
) -> dict[str, object]:
    return {
        "timestamp": "2026-10-31 12:00:00",
        "thread": "main",
        "level": details.level,
        "logger": "example.Service",
        "filename": details.filename,
        "occurrences": [
            {
                "filename": details.filename,
                "line_number": details.line_number,
                "timestamp": "2026-10-31 12:00:00",
                "thread": "main",
            }
        ],
        "message": message,
        "message_body": message_body,
        "stacktrace": "RuntimeError: ignored by message matching",
        "stacktrace_lines": ["RuntimeError: ignored by message matching"],
        "count": details.count,
        "line_numbers": [details.line_number],
    }


def _result(tmp_path: Path) -> AnalysisResult:
    groups = [
        _group(
            "Connection",
            "refused",
            _GroupDetails(
                level="ERROR", count=2, filename="current.log", line_number=1
            ),
        ),
        _group(
            "Connection",
            "refused",
            _GroupDetails(
                level="ERROR", count=3, filename="candidate.log", line_number=4
            ),
        ),
        _group(
            "Connection",
            "refuzed",
            _GroupDetails(
                level="WARN", count=5, filename="other.log", line_number=7
            ),
        ),
        _group(
            "Profile",
            "loaded",
            _GroupDetails(
                level="INFO", count=1, filename="unrelated.log", line_number=9
            ),
        ),
    ]
    for group in groups:
        path = tmp_path / str(group["filename"])
        path.write_text("log line\n", encoding="utf-8")

    return AnalysisResult(
        input_path=str(tmp_path),
        output_path=str(tmp_path / "unused.csv"),
        format_name="CSV",
        keyword="",
        ignore_case=False,
        total_logs=4,
        matched_groups=len(groups),
        matched_occurrences=sum(int(group["count"]) for group in groups),
        level_summary=[],
        selected_levels=(),
        sort_by="time",
        counts=Counter(),
        matched_logs=groups,
        exported_files=[],
    )


class SimilarityHarness(App[None]):
    """以正式應用程式樣式承載事件瀏覽器或相似查找畫面。"""

    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def __init__(self, result: AnalysisResult, *, reference: str | None = None) -> None:
        self.result = result
        self.reference = reference
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("test dashboard")

    def on_mount(self) -> None:
        if self.reference is None:
            self.push_screen(EventBrowserScreen(self.result))
        else:
            self.push_screen(EventSimilarityScreen(self.result, self.reference))


def _option_text(option) -> str:
    return str(option.prompt)


def _assert_similarity_layout(screen, controls) -> None:
    screen_region = screen.region
    for control in controls:
        region = control.region
        assert control.display and region.width > 0 and region.height > 0
        assert region.x >= screen_region.x
        assert region.y >= screen_region.y
        assert region.x + region.width <= screen_region.x + screen_region.width
        assert region.y + region.height <= screen_region.y + screen_region.height

    options = screen.query_one("#similarity-options", OptionList)
    actions = screen.query_one("#similarity-actions")
    assert options.region.height > 0
    assert options.region.y + options.region.height <= actions.region.y


def test_event_detail_opens_prefilled_similarity_and_preserves_navigation(tmp_path: Path) -> None:
    """情境：從事件詳情查找候選並返回時保留原瀏覽位置。"""
    result = _result(tmp_path)

    async def run_check() -> None:
        app = SimilarityHarness(result)
        async with app.run_test(size=(120, 40)) as pilot:
            browser = app.screen
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "occurrences",
                "event occurrence list did not open",
            )
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "preview",
                "event detail preview did not open",
            )

            browser.query_one("#browser-similarity", Button).focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventSimilarityScreen)
                and not app.screen._querying,
                "similarity search did not finish",
            )
            screen = app.screen
            assert screen.query_one("#similarity-reference", Input).value == "Connection refused"
            assert screen.query_one("#similarity-threshold", Input).value == "80"
            assert [item.group_index for item in screen._matches] == [1, 2]
            first_candidate = _option_text(
                screen.query_one("#similarity-options", OptionList).get_option_at_index(0)
            )
            assert "100.0%" in first_candidate
            assert "ERROR" in first_candidate
            assert "3" in first_candidate
            assert "candidate.log:4" in first_candidate

            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventBrowserScreen)
                and app.screen._view == "preview"
                and app.screen._selected_group_index == 1,
                "candidate detail did not open",
            )
            await pilot.click("#close-event-browser")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventSimilarityScreen),
                "similarity list did not return after candidate detail closed",
            )
            assert app.screen.query_one("#similarity-reference", Input).value == "Connection refused"
            assert [item.group_index for item in app.screen._matches] == [1, 2]

            await pilot.click("#similarity-back")
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is browser,
                "original event preview did not return",
            )
            assert browser._view == "preview"
            assert browser._selected_group_index == 0
            assert browser._current_occurrence["filename"] == "current.log"

    asyncio.run(run_check())


def test_manual_similarity_query_and_failure_keep_previous_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    """情境：可手動調整參考與門檻，查詢失敗時保留已顯示候選。"""
    result = _result(tmp_path)

    async def run_check() -> None:
        app = SimilarityHarness(result, reference="Connection refused")
        async with app.run_test(size=(110, 36)) as pilot:
            screen = app.screen
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying,
                "initial similarity search did not finish",
            )
            reference = screen.query_one("#similarity-reference", Input)
            threshold = screen.query_one("#similarity-threshold", Input)
            reference.value = "Profile loaded"
            threshold.value = "100"
            await wait_for_button_ready(app, pilot, "#similarity-search")
            await pilot.click("#similarity-search")
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying
                and screen._applied_query == ("Profile loaded", 100.0),
                "manual similarity search did not finish",
            )
            assert [item.group_index for item in screen._matches] == [3]
            visible_options = [
                _option_text(screen.query_one("#similarity-options", OptionList).get_option_at_index(index))
                for index in range(screen.query_one("#similarity-options", OptionList).option_count)
            ]

            failed_search_started = asyncio.Event()

            async def fail_search(*_args, **_kwargs):
                failed_search_started.set()
                raise OSError("retained results are unavailable")

            monkeypatch.setattr(similarity_screen, "find_similar_groups", fail_search)
            reference.value = "Connection refused"
            threshold.value = "80"
            await wait_for_button_ready(app, pilot, "#similarity-search")
            await pilot.click("#similarity-search")
            await wait_for_ui_state(
                pilot, failed_search_started.is_set, "failed query did not run"
            )
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying
                and "retained results are unavailable"
                in str(screen.query_one("#similarity-status", Static).render()),
                "similarity failure was not reported",
            )
            assert [item.group_index for item in screen._matches] == [3]
            assert [
                _option_text(screen.query_one("#similarity-options", OptionList).get_option_at_index(index))
                for index in range(screen.query_one("#similarity-options", OptionList).option_count)
            ] == visible_options

    asyncio.run(run_check())


def test_closing_similarity_screen_cannot_publish_stale_results(
    tmp_path: Path, monkeypatch
) -> None:
    """情境：關閉期間完成的舊查找不得覆蓋已離開畫面的狀態。"""
    result = _result(tmp_path)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def return_stale_results(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            return [SimilarGroup(group_index=3, score=100.0)]

    monkeypatch.setattr(similarity_screen, "find_similar_groups", return_stale_results)

    async def run_check() -> None:
        app = SimilarityHarness(result, reference="Connection refused")
        async with app.run_test(size=(100, 32)) as pilot:
            screen = app.screen
            await wait_for_ui_state(pilot, started.is_set, "similarity query did not start")
            await pilot.click("#similarity-back")
            await wait_for_ui_state(
                pilot,
                lambda: not isinstance(app.screen, EventSimilarityScreen),
                "similarity screen did not close",
            )
            await wait_for_ui_state(pilot, cancelled.is_set, "closing did not cancel similarity query")
            assert screen._matches == []

    asyncio.run(run_check())


def test_similarity_controls_and_results_fit_narrow_and_wide_screens(tmp_path: Path) -> None:
    """情境：窄寬視窗都能看見參考、門檻、結果與返回操作。"""
    result = _result(tmp_path)

    async def run_check() -> None:
        app = SimilarityHarness(result, reference="Connection refused")
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying,
                "narrow similarity search did not finish",
            )
            controls = (
                screen.query_one("#similarity-reference", Input),
                screen.query_one("#similarity-threshold", Input),
                screen.query_one("#similarity-search", Button),
                screen.query_one("#similarity-options", OptionList),
                screen.query_one("#similarity-previous", Button),
                screen.query_one("#similarity-next", Button),
                screen.query_one("#similarity-back", Button),
            )
            for size_index, size in enumerate(((80, 24), (120, 40), (80, 20))):
                if size_index:
                    await pilot.resize_terminal(*size)
                await wait_for_layout_to_settle(
                    pilot,
                    controls,
                    lambda: all(
                        control.display
                        and control.region.width > 0
                        and control.region.height > 0
                        for control in controls
                    ),
                )
                _assert_similarity_layout(screen, controls)

    asyncio.run(run_check())
