from __future__ import annotations

import asyncio
import pickle
from collections import Counter
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, OptionList, Static

from tui_waits import wait_for_button_ready, wait_for_layout_to_settle, wait_for_ui_state

from log_analyzer.application.analysis.service import AnalysisResult
from log_analyzer.domain.parser import MatchedLogsStore
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.screens.event_browser import EventBrowserScreen
from log_analyzer.presentation.tui.screens.event_correlation import EventCorrelationScreen
from log_analyzer.presentation.tui.screens import event_correlation as correlation_module


class CorrelationHarness(App[None]):
    CSS_PATH = LogAnalyzerApp.CSS_PATH

    def __init__(self, result: AnalysisResult, *, start_browser: bool = False) -> None:
        self.result = result
        self.start_browser = start_browser
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("test dashboard")

    def on_mount(self) -> None:
        if self.start_browser:
            self.push_screen(EventBrowserScreen(self.result))
        else:
            self.push_screen(EventCorrelationScreen(self.result))


def _occurrence(
    filename: str,
    line_number: int,
    timestamp: str,
    *,
    mdc: dict[str, str] | None = None,
) -> dict[str, object]:
    occurrence: dict[str, object] = {
        "filename": filename,
        "line_number": line_number,
        "timestamp": timestamp,
        "thread": "worker-1",
    }
    if mdc is not None:
        occurrence["mdc"] = mdc
    return occurrence


def _group(index: int, occurrences: list[dict[str, object]]) -> dict[str, object]:
    return {
        "timestamp": "2026-10-20T12:00:00",
        "thread": "worker-1",
        "level": "ERROR",
        "logger": f"example.Service{index}",
        "filename": occurrences[0]["filename"] if occurrences else "app.log",
        "occurrences": occurrences,
        "message": f"failure {index}",
        "message_body": f"failure {index}",
        "stacktrace": "",
        "stacktrace_lines": [],
        "count": len(occurrences),
        "line_numbers": [item["line_number"] for item in occurrences],
    }


def _snapshot(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "stable": True}


def _result(
    tmp_path: Path,
    groups: list[dict[str, object]],
    *,
    source_states: dict[str, dict[str, object]] | None = None,
) -> AnalysisResult:
    store_path = tmp_path / "matched-groups.pickle"
    with store_path.open("wb") as file_handle:
        for group in groups:
            pickle.dump(group, file_handle)
    occurrence_count = sum(len(group["occurrences"]) for group in groups)
    states = source_states or {}
    return AnalysisResult(
        input_path=str(tmp_path),
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
        matched_logs=MatchedLogsStore(
            path=str(store_path),
            length=len(groups),
            source_states=states,
        ),
        exported_files=[str(tmp_path / "report.csv")],
        source_states=states,
    )


def _screen(app: CorrelationHarness) -> EventCorrelationScreen:
    assert isinstance(app.screen, EventCorrelationScreen)
    return app.screen


def test_keyboard_search_opens_verified_preview_and_returns_to_same_results(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app.log"
    source_path.write_text("original source line\n", encoding="utf-8")
    groups = [
        _group(
            0,
            [
                _occurrence(
                    "app.log",
                    1,
                    "2026-10-20T12:00:00",
                    mdc={"traceId": "trace-123"},
                )
            ],
        )
    ]
    groups[0]["message"] = "[bold]Failure header[/bold]"
    groups[0]["message_body"] = "continued body"
    result = _result(
        tmp_path,
        groups,
        source_states={"app.log": _snapshot(source_path)},
    )

    async def run_check() -> None:
        app = CorrelationHarness(result)
        async with app.run_test(size=(100, 34)) as pilot:
            screen = _screen(app)
            value_input = screen.query_one("#mdc-value", Input)
            value_input.focus()
            await pilot.press(*"trace-123")
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying and len(screen._matches) == 1,
                "MDC keyboard search did not finish",
            )
            assert screen.query_one("#mdc-key", Input).value == "traceId"
            options = screen.query_one("#correlation-options", OptionList).options
            assert len(options) == 1
            assert "[bold]Failure header[/bold]\ncontinued body" in options[0].prompt.plain
            screen.query_one("#mdc-value", Input).value = "draft-value"
            status = screen.query_one("#correlation-status", Static).render()
            assert "已查詢 traceId=trace-123" in status.plain
            screen.query_one("#mdc-value", Input).value = "trace-123"

            source_path.write_text("changed source line with a different size\n", encoding="utf-8")
            options = screen.query_one("#correlation-options", OptionList)
            options.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventBrowserScreen),
                "selected occurrence did not open the event browser",
            )
            browser = app.screen
            assert browser._view == "preview"
            assert browser.query_one("#source-warning", Static).display
            warning = str(browser.query_one("#source-warning", Static).render())
            assert "來源已變更" in warning

            browser.query_one("#close-event-browser", Button).focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is screen,
                "closing source preview did not return to the correlation results",
            )
            assert value_input.value == "trace-123"
            assert len(screen._matches) == 1
            assert len(screen.query_one("#correlation-options", OptionList).options) == 1

    asyncio.run(run_check())


def test_failed_query_keeps_previous_results_and_names_the_applied_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = [
        _group(
            0,
            [
                _occurrence(
                    "app.log",
                    1,
                    "2026-10-20T12:00:00",
                    mdc={"traceId": "trace-kept"},
                )
            ],
        )
    ]
    result = _result(tmp_path, groups)

    async def fail_query(groups, key, value):
        raise OSError("stored results unavailable")

    async def run_check() -> None:
        app = CorrelationHarness(result)
        async with app.run_test(size=(100, 34)) as pilot:
            screen = _screen(app)
            screen.query_one("#mdc-value", Input).value = "trace-kept"
            await pilot.click("#correlation-search")
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying and len(screen._matches) == 1,
                "initial MDC search did not finish",
            )

            screen.query_one("#mdc-key", Input).value = "requestId"
            screen.query_one("#mdc-value", Input).value = "draft-request"
            monkeypatch.setattr(
                correlation_module,
                "find_correlated_occurrences",
                fail_query,
            )
            await wait_for_button_ready(app, pilot, "#correlation-search")
            await pilot.click("#correlation-search")
            await wait_for_ui_state(
                pilot,
                lambda: "stored results unavailable"
                in screen.query_one("#correlation-status", Static).render().plain,
                "failed query did not report its error",
            )
            assert len(screen._matches) == 1
            assert screen._applied_query == ("traceId", "trace-kept")
            status = screen.query_one("#correlation-status", Static).render().plain
            assert "stored results unavailable" in status, status
            assert "保留上次查詢 traceId=trace-kept 的 1 筆結果" in status
            assert len(screen.query_one("#correlation-options", OptionList).options) == 1

    asyncio.run(run_check())


def test_empty_and_legacy_mdc_results_show_clear_guidance(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        [_group(0, [_occurrence("old.log", 1, "2026-10-20T12:00:00")])],
    )

    async def run_check() -> None:
        app = CorrelationHarness(result)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = _screen(app)
            await pilot.click("#correlation-search")
            status = screen.query_one("#correlation-status", Static).render().plain
            assert "空值不建立關聯" in status
            assert screen._matches == []

            screen.query_one("#mdc-value", Input).value = "unknown-trace"
            await wait_for_button_ready(app, pilot, "#correlation-search")
            await pilot.click("#correlation-search")
            await wait_for_ui_state(
                pilot,
                lambda: "沒有符合的保留事件"
                in screen.query_one("#correlation-status", Static).render().plain,
                "legacy MDC query did not finish",
            )
            status = screen.query_one("#correlation-status", Static).render().plain
            assert "舊結果可能沒有 MDC" in status
            assert "沒有符合的保留事件" in status

    asyncio.run(run_check())


def test_results_are_paginated_to_at_most_one_hundred_rows(tmp_path: Path) -> None:
    occurrences = [
        _occurrence(
            "app.log",
            index + 1,
            f"2026-10-20T12:{index // 60:02d}:{index % 60:02d}",
            mdc={"traceId": "trace-many"},
        )
        for index in range(105)
    ]
    result = _result(tmp_path, [_group(0, occurrences)])

    async def run_check() -> None:
        app = CorrelationHarness(result)
        async with app.run_test(size=(100, 34)) as pilot:
            screen = _screen(app)
            screen.query_one("#mdc-value", Input).value = "trace-many"
            await pilot.click("#correlation-search")
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying and len(screen._matches) == 105,
                "large MDC search did not finish",
            )
            options = screen.query_one("#correlation-options", OptionList)
            assert len(options.options) == 100
            assert not screen.query_one("#correlation-next", Button).disabled

            await pilot.click("#correlation-next")
            await wait_for_ui_state(
                pilot,
                lambda: screen._page_start == 100,
                "next result page did not open",
            )
            assert len(options.options) == 5
            assert screen.query_one("#correlation-prev", Button).disabled is False

            await pilot.click("#correlation-prev")
            await wait_for_ui_state(
                pilot,
                lambda: screen._page_start == 0,
                "previous result page did not open",
            )
            assert len(options.options) == 100

    asyncio.run(run_check())


def test_event_browser_prefills_from_selected_occurrence_at_browse_and_preview_levels(
    tmp_path: Path,
) -> None:
    occurrence = _occurrence(
        "app.log",
        1,
        "2026-10-20T12:00:00",
        mdc={"requestId": "request-7", "traceId": "trace-7", "custom": "custom-7"},
    )
    result = _result(tmp_path, [_group(0, [occurrence])])

    async def run_check() -> None:
        app = CorrelationHarness(result, start_browser=True)
        async with app.run_test(size=(100, 34)) as pilot:
            browser = app.screen
            assert isinstance(browser, EventBrowserScreen)

            await pilot.click("#browser-correlation")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventCorrelationScreen),
                "correlation screen did not open from the event-group list",
            )
            correlation = _screen(app)
            assert correlation.query_one("#mdc-key", Input).value == "traceId"
            assert correlation.query_one("#mdc-value", Input).value == ""
            await pilot.click("#correlation-close")
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is browser,
                "closing group-list correlation did not restore event browsing",
            )

            browser.query_one("#event-options", OptionList).focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "occurrences",
                "group did not open its occurrence positions",
            )

            await pilot.click("#browser-correlation")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventCorrelationScreen),
                "correlation screen did not open from occurrence positions",
            )
            correlation = _screen(app)
            assert correlation.query_one("#mdc-key", Input).value == "traceId"
            assert correlation.query_one("#mdc-value", Input).value == "trace-7"

            await pilot.click("#correlation-close")
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is browser,
                "closing correlation did not restore event browsing",
            )
            browser.query_one("#event-options", OptionList).focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: browser._view == "preview",
                "occurrence did not open its source preview",
            )
            await pilot.click("#browser-correlation")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventCorrelationScreen),
                "correlation screen did not open from source preview",
            )
            correlation = _screen(app)
            assert correlation.query_one("#mdc-key", Input).value == "traceId"
            assert correlation.query_one("#mdc-value", Input).value == "trace-7"

    asyncio.run(run_check())


def test_prefill_uses_current_occurrence_and_trace_request_then_first_key_priority(
    tmp_path: Path,
) -> None:
    groups = [
        _group(
            0,
            [
                _occurrence(
                    "first.log",
                    1,
                    "2026-10-20T12:00:00",
                    mdc={
                        "requestId": "request-first",
                        "traceId": "trace-first",
                        "custom": "custom-first",
                    },
                ),
                _occurrence(
                    "second.log",
                    2,
                    "2026-10-20T12:00:01",
                    mdc={
                        "requestId": "request-second",
                        "traceId": "trace-second",
                    },
                ),
            ],
        ),
        _group(
            1,
            [
                _occurrence(
                    "request.log",
                    1,
                    "2026-10-20T12:00:00",
                    mdc={"custom": "custom-request", "requestId": "request-only"},
                )
            ],
        ),
        _group(
            2,
            [
                _occurrence(
                    "custom.log",
                    1,
                    "2026-10-20T12:00:00",
                    mdc={"firstKey": "first-value", "laterKey": "later-value"},
                )
            ],
        ),
    ]
    groups[0]["mdc"] = {"traceId": "representative-must-not-be-used"}
    result = _result(tmp_path, groups)

    async def run_check() -> None:
        app = CorrelationHarness(result, start_browser=True)
        async with app.run_test(size=(100, 34)) as pilot:
            browser = app.screen
            assert isinstance(browser, EventBrowserScreen)
            await pilot.click("#browser-correlation")
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, EventCorrelationScreen),
                "group-list correlation screen did not open",
            )
            correlation = _screen(app)
            assert correlation.query_one("#mdc-value", Input).value == ""
            await pilot.click("#correlation-close")
            await wait_for_ui_state(pilot, lambda: app.screen is browser, "browser not restored")

            for group_index, expected_key, expected_value in (
                (0, "traceId", "trace-second"),
                (1, "requestId", "request-only"),
                (2, "firstKey", "first-value"),
            ):
                options = browser.query_one("#event-options", OptionList)
                options.highlighted = group_index
                options.focus()
                await pilot.press("enter")
                await wait_for_ui_state(
                    pilot,
                    lambda: browser._view == "occurrences"
                    and browser._selected_group is not None
                    and browser._selected_group.get("logger")
                    == f"example.Service{group_index}",
                    "requested event group did not open",
                )
                if group_index == 0:
                    await pilot.press("down")
                await pilot.click("#browser-correlation")
                await wait_for_ui_state(
                    pilot,
                    lambda: isinstance(app.screen, EventCorrelationScreen),
                    "correlation screen did not open from the selected occurrence",
                )
                correlation = _screen(app)
                assert correlation.query_one("#mdc-key", Input).value == expected_key
                assert correlation.query_one("#mdc-value", Input).value == expected_value
                await pilot.click("#correlation-close")
                await wait_for_ui_state(
                    pilot, lambda: app.screen is browser, "browser not restored"
                )
                await pilot.click("#browser-back")
                await wait_for_ui_state(
                    pilot,
                    lambda: browser._view == "groups",
                    "event group list did not return",
                )

    asyncio.run(run_check())


def test_closing_screen_cancels_in_flight_correlation_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result(tmp_path, [_group(0, [])])
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_query(groups, key, value):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(correlation_module, "find_correlated_occurrences", blocked_query)

    async def run_check() -> None:
        app = CorrelationHarness(result)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = _screen(app)
            screen.query_one("#mdc-value", Input).value = "blocked"
            await pilot.click("#correlation-search")
            await wait_for_ui_state(pilot, started.is_set, "query did not start")
            await pilot.click("#correlation-close")
            await wait_for_ui_state(pilot, cancelled.is_set, "closing did not cancel query")
            assert not isinstance(app.screen, EventCorrelationScreen)

    asyncio.run(run_check())


def test_inputs_results_and_fixed_actions_remain_visible_at_narrow_and_wide_sizes(
    tmp_path: Path,
) -> None:
    result = _result(tmp_path, [_group(0, [])])

    async def run_check() -> None:
        app = CorrelationHarness(result)
        async with app.run_test(size=(80, 24)) as pilot:
            screen = _screen(app)

            def assert_geometry(width: int, height: int) -> None:
                for selector in ("#mdc-key", "#mdc-value", "#correlation-options"):
                    widget = screen.query_one(selector)
                    assert widget.region.width > 0, selector
                    assert widget.region.height >= 1, selector
                for selector in (
                    "#correlation-search",
                    "#correlation-clear",
                    "#correlation-prev",
                    "#correlation-next",
                    "#correlation-close",
                ):
                    button = screen.query_one(selector, Button)
                    assert button.region.width > 0, selector
                    assert button.region.height >= 1, selector
                    assert button.region.y + button.region.height <= height, selector
                assert screen.query_one("#correlation-search", Button).label.plain == "查詢"
                assert screen.query_one("#correlation-close", Button).label.plain == "關閉"
                assert screen.size.width == width

            widgets = [
                screen.query_one(selector)
                for selector in (
                    "#mdc-key",
                    "#mdc-value",
                    "#correlation-options",
                    "#correlation-actions",
                    "#correlation-search",
                    "#correlation-close",
                )
            ]
            await wait_for_layout_to_settle(pilot, widgets)
            assert_geometry(80, 24)
            await pilot.resize_terminal(160, 40)
            await wait_for_layout_to_settle(pilot, widgets)
            assert_geometry(160, 40)
            await pilot.resize_terminal(80, 24)
            await wait_for_layout_to_settle(pilot, widgets)
            assert_geometry(80, 24)

    asyncio.run(run_check())
