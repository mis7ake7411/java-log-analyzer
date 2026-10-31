import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Input, OptionList, Select, Static, Switch

from tui_waits import (
    set_path_input_and_wait,
    wait_for_button_ready,
    wait_for_layout_to_settle,
    wait_for_ui_state,
)
from log_analyzer.application.live.conditions import LiveAnalysisRequest
from log_analyzer.application.events.similarity import message_text
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.components.live_panel import LivePanel
from log_analyzer.presentation.tui.components.live_similarity import (
    LiveSimilaritySettings,
)
from log_analyzer.presentation.tui.screens.live_event import LiveEventScreen
from log_analyzer.presentation.tui.screens.live_summary_details import (
    LiveSummaryDetailsScreen,
)


def _log_line(message: str, *, level: str = "INFO") -> str:
    return f"2026-11-01 10:00:00.000 [main] {level} example.Worker - {message}\n"


async def _set_live_mode(app, pilot) -> None:
    mode = app.query_one("#analysis-mode", Select)
    mode.value = "live"
    await wait_for_ui_state(
        pilot,
        lambda: app._analysis_mode == "live",
        "app did not switch to live mode",
    )


async def _click_live_switch(pilot) -> None:
    switch = pilot.app.query_one("#live-switch", Switch)

    def ready() -> bool:
        region = switch.region
        return switch.visible and region.width > 0 and region.height > 0

    switch.scroll_visible(animate=False)
    await wait_for_layout_to_settle(pilot, (switch,), ready)
    assert await pilot.click(switch), "live tracking switch was not clickable"


async def _set_input_text(pilot, input_widget: Input, value: str) -> None:
    input_widget.scroll_visible(animate=False)
    await wait_for_layout_to_settle(pilot, (input_widget,))
    input_widget.value = ""
    await wait_for_ui_state(
        pilot,
        lambda: input_widget.value == "",
        f"input {input_widget.id} did not clear",
    )
    input_widget.focus()
    await wait_for_ui_state(
        pilot,
        lambda: pilot.app.focused is input_widget,
        f"input {input_widget.id} did not receive focus",
    )
    await pilot.press(
        *("space" if character == " " else character for character in value)
    )
    await wait_for_ui_state(
        pilot,
        lambda: input_widget.value == value,
        f"input {input_widget.id} did not retain typed text",
    )


async def _enable_similarity(pilot, reference: str, threshold: str = "100"):
    app = pilot.app
    settings = app.query_one("#live-similarity-settings", LiveSimilaritySettings)
    checkbox = app.query_one("#live-similarity-enabled", Checkbox)
    checkbox.scroll_visible(animate=False)
    await wait_for_layout_to_settle(pilot, (settings, checkbox))
    if not checkbox.value:
        checkbox.focus()
        await wait_for_ui_state(
            pilot,
            lambda: app.focused is checkbox,
            "similarity checkbox did not receive focus",
        )
        await pilot.press("space")
        await wait_for_ui_state(
            pilot,
            lambda: (
                checkbox.value and app.query_one("#live-similarity-details").display
            ),
            "similarity fields did not expand",
        )
    await _set_input_text(
        pilot, app.query_one("#live-similarity-reference", Input), reference
    )
    await _set_input_text(
        pilot, app.query_one("#live-similarity-threshold", Input), threshold
    )
    await wait_for_ui_state(
        pilot,
        lambda: _identity_matches(settings, (reference, float(threshold))),
        "similarity condition was not validated",
    )
    return settings


def _identity_matches(settings, expected) -> bool:
    try:
        return settings.get_identity() == expected
    except ValueError:
        return False


async def _wait_for_session_state(pilot, state: str, *, matched: int | None = None):
    app = pilot.app
    await wait_for_ui_state(
        pilot,
        lambda: (
            app._live_session is not None
            and app._live_session.state == state
            and (matched is None or app._live_session.matched == matched)
        ),
        f"live session did not reach state={state!r}, matched={matched!r}",
    )


async def _wait_for_completed_or_error(pilot, session, completed: int, message: str):
    app = pilot.app
    observed = {}

    def finished() -> bool:
        observed.update(
            state=session.state,
            completed=session.completed,
            matched=session.matched,
            offset=session.offset,
            backlog=session.backlog,
            pending=session.has_pending,
            error=session.error,
            runner_running=app._live_runner.running if app._live_runner else None,
            runner_failure=app._live_runner.failure if app._live_runner else None,
        )
        return session.completed >= completed or session.state == "error"

    try:
        await wait_for_ui_state(pilot, finished, message)
    except AssertionError as exc:
        raise AssertionError(f"{exc}; latest_session_state={observed!r}") from exc


def test_live_similarity_starts_filters_appends_locks_and_requires_rebuild(  # noqa: PLR0915 - preserve the live filter and changed-condition workflow
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "app.log"
    source.write_text(
        _log_line("Database unavailable")
        + _log_line("database unavailable")
        + _log_line("Connection refused"),
        encoding="utf-8",
    )
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():  # noqa: PLR0915 - keep the complete start, append, pause, and rebuild flow
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            settings = app.query_one(
                "#live-similarity-settings", LiveSimilaritySettings
            )
            assert not settings.display
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            await _set_live_mode(app, pilot)
            assert settings.display

            settings = await _enable_similarity(pilot, "Database unavailable")
            threshold = app.query_one("#live-similarity-threshold", Input)
            threshold.value = "nan"
            await wait_for_ui_state(
                pilot,
                lambda: (
                    "尚未有效"
                    in app.query_one("#analysis-mode-status", Static).render().plain
                ),
                "invalid input should be reported without escaping the change handler",
            )
            assert threshold.value == "nan"
            threshold.value = "100"
            await wait_for_ui_state(
                pilot,
                lambda: _identity_matches(settings, ("Database unavailable", 100.0)),
                "correcting an invalid threshold should restore a valid condition",
            )

            await _click_live_switch(pilot)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    app._live_session is not None
                    or (
                        app._live_operation_task is not None
                        and app._live_operation_task.done()
                    )
                ),
                "tracking start did not publish a session or visible error",
            )
            session = app._live_session
            assert session is not None, (
                app.query_one("#analysis-mode-status", Static).render().plain
            )
            assert isinstance(session.conditions, LiveAnalysisRequest)
            assert session.conditions.similar_to == "Database unavailable"
            assert session.conditions.similarity_threshold == 100.0
            await _wait_for_completed_or_error(
                pilot,
                session,
                2,
                "live parser did not finish complete existing entries",
            )
            assert session.state == "tracking", session.error
            assert session.matched == 1
            assert session.has_pending
            assert app.query_one("#live-similarity-enabled", Checkbox).disabled
            assert app.query_one("#live-similarity-reference", Input).disabled
            assert app.query_one("#live-similarity-threshold", Input).disabled

            with source.open("a", encoding="utf-8") as stream:
                stream.write(
                    _log_line("Database unavailable")
                    + _log_line("database unavailable")
                )
            await wait_for_ui_state(
                pilot,
                lambda: session.completed == 4 or session.state == "error",
                "append did not flow through the live reader",
            )
            assert session.state == "tracking", session.error
            assert session.matched == 2

            await _click_live_switch(pilot)
            await _wait_for_session_state(pilot, "paused", matched=2)
            assert not app.query_one("#live-similarity-reference", Input).disabled
            manage_button = app.query_one("#manage-recipes", Button)
            assert manage_button.display and not manage_button.disabled

            reference = app.query_one("#live-similarity-reference", Input)
            reference.value = "Connection refused"
            await wait_for_ui_state(
                pilot,
                lambda: settings.get_identity() == ("Connection refused", 100.0),
                "paused similarity reference edit was not accepted",
            )
            await _click_live_switch(pilot)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    session.state == "paused"
                    and "重新分析來源"
                    in app.query_one("#analysis-mode-status", Static).render().plain
                ),
                "changed fixed conditions should block direct resume and request rebuild",
            )
            assert app._live_session is session
            assert not app._live_runner.running
            assert session.matched == 2

            rebuild = app.query_one("#live-rebuild", Button)
            rebuild.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#live-rebuild")
            assert await pilot.click(rebuild)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    app._live_session is not session
                    and app._live_session.state == "paused"
                ),
                "rebuild did not publish a paused replacement session",
            )
            replacement = app._live_session
            assert isinstance(replacement.conditions, LiveAnalysisRequest)
            assert replacement.conditions.similar_to == "Connection refused"
            assert replacement.conditions.similarity_threshold == 100.0
            assert replacement.completed == 4
            assert replacement.matched == 1

    asyncio.run(run_check())


def test_batch_run_button_visibility_tracks_analysis_mode():
    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            run_button = app.query_one("#run", Button)
            await wait_for_layout_to_settle(pilot, (run_button,))
            assert run_button.display

            await _set_live_mode(app, pilot)
            await wait_for_ui_state(
                pilot,
                lambda: not run_button.display,
                "batch run action remained visible in live mode",
            )
            manage_button = app.query_one("#manage-recipes", Button)
            assert manage_button.display and not manage_button.disabled

            mode = app.query_one("#analysis-mode", Select)
            mode.value = "batch"
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "batch" and run_button.display,
                "batch run action was not restored after returning to batch mode",
            )

    asyncio.run(run_check())


def test_live_event_can_fill_reference_only_after_pause_and_summary_keeps_original_condition(  # noqa: PLR0915 - preserve reference selection and captured summary metadata
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "event.log"
    source.write_text(_log_line("request body") + _log_line("filler"), encoding="utf-8")
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():  # noqa: PLR0915 - keep event actions and summary metadata in one workflow
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            await _set_live_mode(app, pilot)
            settings = await _enable_similarity(pilot, "original reference", "0")
            await _click_live_switch(pilot)
            session = app._live_session
            await _wait_for_completed_or_error(
                pilot,
                session,
                1,
                "tracking did not save the event or report a visible error",
            )
            assert session.state == "tracking", (
                session.error,
                app._live_runner.failure,
                app.query_one("#analysis-mode-status", Static).render().plain,
            )
            assert session.completed == 1
            original_session_id = session.session_id
            event = next(session.iter_page(0, 1))
            expected_reference = message_text(event)
            original_offset = session.offset
            assert expected_reference == "request body"

            panel = app.query_one("#live-panel", LivePanel)
            events = panel.query_one("#live-events", OptionList)
            await wait_for_layout_to_settle(pilot, (events,))
            assert await pilot.click(events, offset=(2, 1))
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, LiveEventScreen),
                "live event details did not open",
            )

            use_reference = app.screen.query_one(
                "#live-event-use-as-similarity-reference", Button
            )
            await wait_for_button_ready(
                app, pilot, "#live-event-use-as-similarity-reference"
            )
            assert await pilot.click(use_reference)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    "請先暫停追蹤"
                    in app.screen.query_one("#live-event-reference-status", Static)
                    .render()
                    .plain
                ),
                "tracking event action should ask the user to pause first",
            )
            assert isinstance(app.screen, LiveEventScreen)
            assert settings.get_identity() == ("original reference", 0.0)
            assert app._live_session is session
            assert session.offset == original_offset

            await pilot.press("escape")
            await wait_for_ui_state(
                pilot,
                lambda: not isinstance(app.screen, LiveEventScreen),
                "live event details did not close",
            )
            await _click_live_switch(pilot)
            await _wait_for_session_state(pilot, "paused", matched=1)
            paused_offset = session.offset

            await wait_for_layout_to_settle(pilot, (events,))
            assert await pilot.click(events, offset=(2, 1))
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, LiveEventScreen),
                "paused live event details did not reopen",
            )
            use_reference = app.screen.query_one(
                "#live-event-use-as-similarity-reference", Button
            )
            await wait_for_button_ready(
                app, pilot, "#live-event-use-as-similarity-reference"
            )
            assert await pilot.click(use_reference)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    not isinstance(app.screen, LiveEventScreen)
                    and settings.get_identity() == (expected_reference, 0.0)
                ),
                "paused event did not fill the similarity reference",
            )
            assert app._live_session is session
            assert session.session_id == original_session_id
            assert session.offset == paused_offset
            assert session.state == "paused"
            assert not app._live_runner.running
            assert app._live_operation_task is None

            summary = app.query_one("#live-summary", Button)
            await wait_for_button_ready(app, pilot, "#live-summary")
            assert await pilot.click(summary)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    app._live_summary_result is not None
                    and app._live_operation_task is None
                ),
                "tracking summary snapshot did not finish",
            )
            assert not app.query_one("#run", Button).display
            details = app.query_one("#live-summary-details", Button)
            await wait_for_button_ready(app, pilot, "#live-summary-details")
            assert await pilot.click(details)
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, LiveSummaryDetailsScreen),
                "tracking snapshot details did not open",
            )
            detail_text = (
                app.screen.query_one("#live-summary-details-content", Static)
                .render()
                .plain
            )
            assert "original reference" in detail_text
            assert expected_reference not in detail_text
            assert "門檻 0" in detail_text

    asyncio.run(run_check())


def test_live_event_reference_and_close_buttons_fit_short_viewport():
    event = {"message": "saved body", "raw_text": "saved body\n"}

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(80, 20)) as pilot:
            screen = LiveEventScreen(event)
            app.push_screen(screen)
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is screen,
                "live event screen did not open in short viewport",
            )
            reference = screen.query_one(
                "#live-event-use-as-similarity-reference", Button
            )
            await wait_for_layout_to_settle(pilot, (reference,))
            region = reference.region
            assert reference.visible and region.width > 0 and region.height > 0
            assert region.x >= 0 and region.y >= 0
            assert region.x + region.width <= 80
            assert region.y + region.height <= 20
            assert await pilot.click(reference), "reference action was not clickable"
            await wait_for_ui_state(
                pilot,
                lambda: not isinstance(app.screen, LiveEventScreen),
                "reference action did not close the event details",
            )

            screen = LiveEventScreen(event)
            app.push_screen(screen)
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is screen,
                "live event screen did not reopen",
            )
            close = screen.query_one("#live-event-close", Button)
            await wait_for_layout_to_settle(pilot, (close,))
            region = close.region
            assert close.visible and region.width > 0 and region.height > 0
            assert region.x >= 0 and region.y >= 0
            assert region.x + region.width <= 80
            assert region.y + region.height <= 20
            assert await pilot.click(close), "close action was not clickable"
            await wait_for_ui_state(
                pilot,
                lambda: not isinstance(app.screen, LiveEventScreen),
                "close action did not dismiss event details",
            )

    asyncio.run(run_check())


@pytest.mark.parametrize("size", [(80, 20), (160, 44)])
def test_live_similarity_controls_remain_usable_in_short_and_wide_layouts(
    tmp_path: Path, monkeypatch, size
):
    source = tmp_path / "layout.log"
    source.write_text(_log_line("layout body"), encoding="utf-8")
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            await _set_live_mode(app, pilot)
            settings = await _enable_similarity(pilot, "layout body", "80")
            host = app.query_one("#form-panel")
            for selector in (
                "#live-similarity-settings",
                "#live-similarity-enabled",
                "#live-similarity-reference",
                "#live-similarity-threshold",
                "#live-similarity-hint",
            ):
                widget = app.query_one(selector)
                await wait_for_layout_to_settle(pilot, (widget,))
                assert widget.region.width > 0
                assert widget.region.x >= host.content_region.x
                assert (
                    widget.region.x + widget.region.width
                    <= host.content_region.x + host.content_region.width
                )
            assert settings.get_identity() == ("layout body", 80.0)

    asyncio.run(run_check())
