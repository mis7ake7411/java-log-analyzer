import asyncio
import json
import threading

import pytest

from textual.widgets import Input, OptionList, Select, Static, Switch

from tui_waits import (
    set_path_input_and_wait,
    wait_for_layout_to_settle,
    wait_for_button_ready,
    wait_for_export_confirmation_layout,
    wait_for_export_to_finish,
    wait_for_ui_state,
    wait_for_scheduled_analysis,
    wait_until,
)

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.components.live_panel import LivePanel
from log_analyzer.presentation.tui.controllers import live as live_controller
from log_analyzer.domain.analysis_control import AnalysisControl
from log_analyzer.presentation.tui.screens.live_event import LiveEventScreen
from log_analyzer.presentation.tui.screens.pattern_confirmation import (
    LogbackPatternConfirmationScreen,
)


def test_live_mode_reads_pauses_resumes_and_rejects_changed_conditions(  # noqa: PLR0915 - preserve the complete live start, pause, resume, and guard scenario
    tmp_path, monkeypatch
):
    source = tmp_path / "app.log"
    initial = _log_line("first") + _log_line("second")
    source.write_text(initial, encoding="utf-8")
    initial_size = source.stat().st_size
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            mode = app.query_one("#analysis-mode", Select)
            panel = app.query_one("#live-panel", LivePanel)
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )

            assert mode.value == "batch"
            assert not panel.display

            mode.value = "live"
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "switching to live mode should show the tracking panel",
            )

            unrestricted = app.query_one("#level_unrestricted")
            unrestricted.value = True
            await wait_until(
                lambda: app.query_one("#level_INFO").disabled,
                "unrestricted level control should disable individual levels",
            )

            switch = panel.query_one("#live-switch", Switch)
            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                or app._pending_live_pattern is not None
                or (
                    app._live_operation_task is not None
                    and app._live_operation_task.done()
                ),
                "live start should publish a session or finish with a visible error",
            )
            assert app._pending_live_pattern is None, "default pattern should not prompt"
            session = app._live_session
            assert session is not None, app.query_one(
                "#analysis-mode-status"
            ).render().plain
            await wait_for_ui_state(
                pilot,
                lambda: session.completed >= 1 or session.state == "error",
                "live tracking should finish the first entry and retain its tail",
            )
            assert session.offset == initial_size
            assert session.completed == 1, (
                session.state,
                session.error,
                app._live_runner.failure,
                app.query_one("#analysis-mode-status").render().plain,
            )
            assert session.state == "tracking"
            assert session.has_pending
            assert app.query_one("#path", Input).disabled
            assert app.query_one("#level_INFO").disabled

            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "paused" and not app._live_runner.running,
                "pause should finish the active reader handoff",
            )
            paused_offset = session.offset
            with source.open("a", encoding="utf-8") as stream:
                stream.write(_log_line("third"))
            assert source.stat().st_size > paused_offset
            assert session.offset == paused_offset
            assert app.query_one("#level_INFO").disabled

            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: session.completed >= 2 and session.state == "tracking",
                "resume should capture appended data without restarting the source",
            )
            assert session.matched == 2
            assert session.has_pending

            # A modified form cannot silently change the existing session's
            # fixed analysis request when the user tries to resume again.
            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "paused" and not app._live_runner.running,
                "second pause should finish before editing conditions",
            )
            keyword = app.query_one("#keyword", Input)
            keyword.value = "different"
            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "paused"
                and "條件已變更"
                in app.query_one("#analysis-mode-status").render().plain,
                "changed conditions should require rebuilding the session",
            )
            assert session.matched == 2
            assert not app._live_runner.running

    asyncio.run(run_check())


def _log_line(message, *, level="INFO"):
    return (
        f"2026-10-31 10:00:00.000 [main] {level} example.Worker - {message}\n"
    )


async def _click_live_switch(pilot, switch):
    app = pilot.app

    def has_visible_bounds():
        region = switch.region
        return (
            switch.visible
            and region.width > 0
            and region.height > 0
        )

    await wait_for_layout_to_settle(
        pilot,
        (switch,),
        has_visible_bounds,
    )
    region = switch.region
    x = region.x + min(1, region.width - 1)
    y = region.y + min(1, region.height - 1)
    if not app.screen.region.contains(x, y):
        ancestors = []
        widget = switch
        while widget is not None:
            ancestors.append(
                f"{type(widget).__name__}(id={widget.id!r}, "
                f"region={getattr(widget, 'region', None)!r}, "
                f"content={getattr(widget, 'content_region', None)!r}, "
                f"scroll={getattr(widget, 'scroll_offset', None)!r})"
            )
            widget = widget.parent
        raise AssertionError(
            f"live switch is outside the visible screen: switch={region!r}, "
            f"screen={app.screen.region!r}, ancestors={ancestors!r}, "
            f"form-panel={app.query_one('#form-panel').region!r}, "
            f"output-panel={app.query_one('#output-panel').region!r}"
        )
    assert await pilot.click("#live-switch"), "live switch was not clickable"


async def _click_live_panel_action(pilot, panel, selector):
    await wait_for_button_ready(pilot.app, pilot, selector)
    assert await pilot.click(selector), f"{selector} was not clickable"


async def _click_summary_return(pilot, app):
    button = app.query_one("#return-live")
    button.scroll_visible(animate=False)
    await wait_for_button_ready(app, pilot, "#return-live")
    assert await pilot.click("#return-live"), "return-to-live button was not clickable"


def test_live_event_screen_shows_saved_unicode_metadata_and_raw_text():
    event = {
        "filename": "應用程式.log",
        "generation": 2,
        "line_num": 17,
        "sequence": 31,
        "timestamp": "2026-11-01 12:34:56.789",
        "level": "WARN",
        "thread": "worker-一",
        "logger": "應用.服務",
        "mdc": {"使用者": "甲[乙]", "trace": "abc-123"},
        "raw_text": "原始文字 [保留] MDC: 使用者=甲[乙]\n續行一",
    }

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 34)) as pilot:
            screen = LiveEventScreen(event)
            app.push_screen(screen)
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is screen
                and any(
                    widget.is_mounted
                    for widget in screen.query("#live-event-raw")
                ),
                "saved event details should be mounted",
            )
            metadata = screen.query_one("#live-event-metadata", Static).render().plain
            mdc = screen.query_one("#live-event-mdc", Static).render().plain
            raw = screen.query_one("#live-event-raw", Static).render().plain
            assert "來源檔：應用程式.log" in metadata
            assert "來源世代：2" in metadata
            assert "起始行：17" in metadata
            assert "接收序號：31" in metadata
            assert "時間：2026-11-01 12:34:56.789" in metadata
            assert "Logger：應用.服務" in metadata
            assert "\\u" not in metadata
            assert "使用者：甲[乙]" in mdc
            assert "trace：abc-123" in mdc
            assert raw == event["raw_text"]
            await pilot.press("escape")

    asyncio.run(run_check())


def test_live_mode_restores_batch_zero_result_action_visibility(monkeypatch):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            actions = app.query_one("#zero-result-actions")
            app._set_result_status("no_results")
            assert not actions.has_class("zero-results-hidden")
            assert actions.styles.display == "block"

            mode = app.query_one("#analysis-mode", Select)
            mode.value = "live"
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and not actions.display,
                "live panel should hide the batch-only retry actions",
            )
            mode.value = "batch"
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "batch"
                and actions.styles.display == "block",
                "batch mode should defer retry visibility to its result state",
            )
            assert not actions.has_class("zero-results-hidden")

            app._set_result_status("idle")
            assert actions.has_class("zero-results-hidden")
            assert actions.styles.display == "none"

    asyncio.run(run_check())


def test_confirmed_uncertain_xml_pattern_keeps_resume_identity(tmp_path, monkeypatch):
    source = tmp_path / "app.log"
    initial = (
        "2026-10-24 12:00:00.123 [main] INFO  com.test.App - first\n"
        "2026-10-24 12:00:01.123 [main] INFO  com.test.App - tail\n"
    )
    source.write_text(initial, encoding="utf-8")
    pattern = (
        "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n"
    )
    tied_pattern = pattern.replace("%msg", "%message")
    xml = tmp_path / "logback.xml"
    xml.write_text(
        "<configuration>"
        f'<property name="FIRST_PATTERN" value="{pattern}"/>'
        f'<property name="SECOND_PATTERN" value="{tied_pattern}"/>'
        "</configuration>",
        encoding="utf-8",
    )
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            mode = app.query_one("#analysis-mode", Select)
            mode.value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live mode should be ready to confirm the uncertain XML pattern",
            )

            switch = panel.query_one("#live-switch", Switch)
            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: isinstance(app.screen, LogbackPatternConfirmationScreen),
                "uncertain XML candidates should require explicit selection",
            )
            assert app._live_session is None
            select_pattern = app.screen.query_one("#pattern-select-0")
            select_pattern.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#pattern-select-0")
            assert await pilot.click("#pattern-select-0")

            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                and app._live_session.completed >= 1,
                "confirmed pattern should start live reading",
            )
            session = app._live_session
            assert session.conditions.log_pattern == pattern
            assert session.matched == 1

            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "paused" and not app._live_runner.running,
                "confirmed-pattern session should pause safely",
            )
            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "tracking" and app._live_runner.running,
                "confirmed XML conditions should remain valid on resume",
            )
            assert session.matched == 1
            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "paused" and not app._live_runner.running,
                "resumed confirmed-pattern session should pause safely",
            )

    asyncio.run(run_check())


def test_live_directory_source_is_rejected_without_creating_a_session(
    tmp_path, monkeypatch
):
    source = tmp_path / "logs"
    source.mkdir()
    (source / "app.log").write_text(_log_line("one"), encoding="utf-8")
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            app.query_one("#analysis-mode", Select).value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live mode should be visible",
            )
            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            await wait_for_ui_state(
                pilot,
                lambda: app._live_operation_task is None
                and "單一檔案來源" in app.query_one("#analysis-mode-status").render().plain,
                "directory sources should be rejected with an explanation",
            )
            assert app._live_session is None
            assert app._live_runner is None

    asyncio.run(run_check())


def test_stop_keeps_events_and_rebuild_failure_cancel_success_are_safe(  # noqa: PLR0915 - preserve stop, failure, cancellation, and successful replacement checks
    tmp_path, monkeypatch
):
    initial = "".join(_log_line(message) for message in ("one", "two", "tail"))
    source = tmp_path / "app.log"
    source.write_bytes(initial.encode("utf-8"))
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    real_prepare = live_controller.prepare_replacement
    block_prepare = {"value": False}

    async def optionally_block_prepare(previous, conditions, **kwargs):
        if block_prepare["value"]:
            preparation_started.set()
            await cancel_prepare.wait()
        return await real_prepare(previous, conditions, **kwargs)

    preparation_started = None
    cancel_prepare = None
    monkeypatch.setattr(
        live_controller, "prepare_replacement", optionally_block_prepare
    )

    async def run_check():  # noqa: PLR0915 - retain the full rebuild lifecycle interaction
        nonlocal preparation_started, cancel_prepare
        preparation_started = asyncio.Event()
        cancel_prepare = asyncio.Event()
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            app.query_one("#analysis-mode", Select).value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live mode should be visible",
            )
            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                and app._live_session.completed >= 2,
                "initial complete events should be captured before stop",
            )
            old = app._live_session

            await _click_live_panel_action(pilot, panel, "#live-stop")
            await wait_for_ui_state(
                pilot,
                lambda: old.state == "stopped" and not app._live_runner.running,
                "stop should finish the captured tail before retaining the store",
            )
            assert not old._closed
            assert old.matched == 3
            assert [event["message"] for event in old.iter_page(0, 10)] == [
                "one",
                "two",
                "tail",
            ]

            await _click_live_panel_action(pilot, panel, "#live-summary")
            await wait_for_ui_state(
                pilot,
                lambda: app._live_summary_active
                and app._live_summary_result is not None,
                "stopped events should remain available to summaries",
            )
            summary = app._live_summary_result
            assert summary.matched_occurrences == 3
            assert summary.tracking_snapshot.cutoff == 3

            await _click_summary_return(pilot, app)
            missing = tmp_path / "missing.log"
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(missing)
            )
            await _click_live_panel_action(pilot, panel, "#live-rebuild")
            await wait_for_ui_state(
                pilot,
                lambda: app._live_operation_task is None
                and "重新分析來源失敗"
                in app.query_one("#analysis-mode-status").render().plain,
                "failed replacement should report an error and preserve stopped data",
            )
            assert app._live_session is old
            assert not old._closed
            assert old.matched == 3

            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            block_prepare["value"] = True
            await _click_live_panel_action(pilot, panel, "#live-rebuild")
            await wait_until(
                preparation_started.is_set,
                "replacement preparation should reach the cancellable boundary",
            )
            cancel_button = app.query_one("#cancel-analysis")
            cancel_button.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#cancel-analysis")
            assert await pilot.click("#cancel-analysis")
            await wait_for_ui_state(
                pilot,
                lambda: app._live_operation_task is None
                and "重新分析來源已取消"
                in app.query_one("#analysis-mode-status").render().plain,
                "cancelled replacement should leave the prior session intact",
            )
            assert app._live_session is old
            assert old.state == "stopped"
            assert old.matched == 3
            block_prepare["value"] = False

            await _click_live_panel_action(pilot, panel, "#live-rebuild")
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not old
                and app._live_session.state == "paused",
                "successful replacement should publish a new paused session",
            )
            new = app._live_session
            assert old._closed
            assert new.session_id != old.session_id
            assert new.matched == 2
            assert new.has_pending
            assert summary.tracking_snapshot.cutoff == 3
            assert len(summary.matched_logs) == 3

    asyncio.run(run_check())


def test_batch_result_roundtrip_clear_and_enter_do_not_disturb_live_session(
    tmp_path, monkeypatch
):
    source = tmp_path / "app.log"
    source.write_bytes(
        (_log_line("first") + _log_line("tail")).encode("utf-8")
    )
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            await wait_for_button_ready(app, pilot, "#run")
            assert await pilot.click("#run")
            await wait_for_scheduled_analysis(app)
            batch_result = app._last_result
            assert batch_result is not None
            assert batch_result.matched_occurrences == 2

            mode = app.query_one("#analysis-mode", Select)
            mode.value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live mode should keep batch result stashed",
            )
            assert app._last_result is None
            assert app._batch_result is batch_result

            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                and app._live_session.completed >= 1,
                "live session should independently capture the existing source",
            )
            session = app._live_session
            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "paused" and not app._live_runner.running,
                "live session should pause before switching modes",
            )

            scheduled_batch = app._scheduled_analysis_task
            app.set_focus(None)
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: "即時追蹤模式不會啟動批次分析"
                in app.query_one("#analysis-mode-status").render().plain,
                "Enter in live mode should explain that batch analysis did not start",
            )
            assert app._scheduled_analysis_task is scheduled_batch
            assert app._live_session is session

            mode.value = "batch"
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "batch" and app._last_result is batch_result,
                "batch result should be restored independently",
            )
            assert list(batch_result.matched_logs)

            clear_button = app.query_one("#clear")
            clear_button.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#clear")
            assert await pilot.click("#clear")
            await wait_for_ui_state(
                pilot,
                lambda: app._batch_result is None and app._last_result is None,
                "clearing batch results should release only the batch result",
            )
            assert app._live_session is session
            assert session.state == "paused"
            assert session.matched == 1

            mode.value = "live"
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "returning to live mode should preserve its paused session",
            )
            assert app._live_session is session
            assert [event["message"] for event in session.iter_page(0, 10)] == [
                "first",
            ]

    asyncio.run(run_check())


def test_summary_cancel_and_app_shutdown_wait_for_worker_cleanup(  # noqa: PLR0915 - cover both cancellation and quit ownership paths
    tmp_path, monkeypatch
):
    source = tmp_path / "app.log"
    source.write_text(_log_line("first"), encoding="utf-8")
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    worker_started = threading.Event()
    worker_release = threading.Event()
    snapshots = []
    original_builder = live_controller.build_tracking_summary
    original_cancel = AnalysisControl.cancel

    def gated_builder(snapshot, *, control=None):
        snapshots.append(snapshot)
        worker_started.set()
        if not worker_release.wait(timeout=10):
            raise AssertionError("summary worker did not receive cancellation")
        return original_builder(snapshot, control=control)

    def cancel_and_release(control):
        original_cancel(control)
        worker_release.set()

    monkeypatch.setattr(live_controller, "build_tracking_summary", gated_builder)
    monkeypatch.setattr(AnalysisControl, "cancel", cancel_and_release)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            app.query_one("#analysis-mode", Select).value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live mode should be ready for cancellable summaries",
            )
            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                and app._live_session.state == "tracking",
                "live session should start before summary cancellation",
            )
            session = app._live_session

            await _click_live_panel_action(pilot, panel, "#live-summary")
            await wait_until(
                worker_started.is_set,
                "summary worker should reach the cancellation boundary",
            )
            cancel_button = app.query_one("#cancel-analysis")
            cancel_button.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#cancel-analysis")
            assert await pilot.click("#cancel-analysis")
            await wait_for_ui_state(
                pilot,
                lambda: app._live_operation_task is None
                and "追蹤摘要已取消"
                in app.query_one("#analysis-mode-status").render().plain,
                "user cancellation should wait for the worker then release its snapshot",
            )
            assert app._live_session is session
            assert session.state == "tracking"
            assert not session._closed
            with pytest.raises(RuntimeError, match="released"):
                list(snapshots[0].events.iter_page(0, 1))
            assert app._live_pending_snapshot is None
            assert app._live_summary_result is None

            worker_started.clear()
            worker_release.clear()
            await _click_live_panel_action(pilot, panel, "#live-summary")
            await wait_until(
                worker_started.is_set,
                "second summary worker should start before application exit",
            )

        assert session._closed
        assert app._live_pending_snapshot is None
        assert app._live_summary_control is None
        assert app._live_summary_result is None
        with pytest.raises(RuntimeError, match="released"):
            list(snapshots[1].events.iter_page(0, 1))


def test_empty_tracking_summary_succeeds_and_clear_keeps_session(tmp_path, monkeypatch):
    source = tmp_path / "empty.log"
    source.write_bytes(b"")
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            app.query_one("#analysis-mode", Select).value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live mode should be visible for an empty source",
            )
            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                and app._live_session.state == "tracking",
                "empty source should still start a live session",
            )
            session = app._live_session
            await _click_live_panel_action(pilot, panel, "#live-summary")
            await wait_for_ui_state(
                pilot,
                lambda: app._live_summary_active
                and app._live_summary_result is not None,
                "empty snapshots should publish a successful zero summary",
            )
            result = app._live_summary_result
            assert result.total_logs == 0
            assert result.matched_groups == 0
            assert result.matched_occurrences == 0
            assert len(result.matched_logs) == 0
            assert app._result_state == "complete"

            clear_button = app.query_one("#clear")
            clear_button.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#clear")
            assert await pilot.click("#clear")
            await wait_for_ui_state(
                pilot,
                lambda: not app._live_summary_active
                and app._live_summary_result is None
                and panel.display,
                "clearing the snapshot should return to live without ending session",
            )
            assert app._live_session is session
            assert session.state == "tracking"
            assert not session._closed
            with pytest.raises(RuntimeError, match="released"):
                list(result.matched_logs)

    asyncio.run(run_check())


@pytest.mark.parametrize("height", (24, 20))
def test_full_app_live_controls_fit_and_work_at_compact_short_width(  # noqa: PLR0915 - retain the complete compact geometry workflow
    tmp_path, monkeypatch, height
):
    source = tmp_path / f"app-{height}.log"
    source.write_bytes((_log_line("first") + _log_line("tail")).encode("utf-8"))
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():  # noqa: PLR0915 - preserve the end-to-end geometry scenario
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            app.query_one("#analysis-mode", Select).value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live mode should be visible before compact resize",
            )
            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                and app._live_session.state == "tracking"
                and app._live_session.completed >= 1,
                "live reader should be tracking before resize",
            )

            await pilot.resize_terminal(80, height)
            shell = app.query_one("#shell")
            content = app.query_one("#content")
            form_panel = app.query_one("#form-panel")
            output_panel = app.query_one("#output-panel")
            controls = (
                app.query_one("#analysis-mode-field"),
                app.query_one("#analysis-mode"),
                panel,
                panel.query_one("#live-switch"),
                panel.query_one("#live-rebuild"),
                panel.query_one("#live-summary"),
                panel.query_one("#live-stop"),
                panel.query_one("#live-follow"),
                panel.query_one("#live-events"),
            )
            await wait_for_layout_to_settle(
                pilot,
                (shell, content, form_panel, output_panel, *controls),
                lambda: shell.has_class("compact")
                and shell.has_class("short")
                and content.has_class("compact")
                and content.has_class("short")
                and panel.region.width > 0,
            )
            assert all(
                widget.region.width > 0
                and widget.region.x >= 0
                and widget.region.x + widget.region.width <= 80
                for widget in controls
            )
            assert shell.has_class("compact") and shell.has_class("short")
            assert form_panel.region.x == output_panel.region.x
            assert form_panel.region.x >= 0
            assert form_panel.region.x + form_panel.region.width <= 80
            assert output_panel.region.x >= 0
            assert output_panel.region.x + output_panel.region.width <= 80
            if height == 20:
                switch = panel.query_one("#live-switch", Switch)
                rebuild = panel.query_one("#live-rebuild")
                status = panel.query_one("#live-status", Static)
                events = panel.query_one("#live-events", OptionList)
                live_scroll = panel.query_one("#live-content-scroll")
                fixed_regions = (switch.region, rebuild.region)

                def assert_visible_hit(widget):
                    regions = (
                        widget.region,
                        live_scroll.region,
                        output_panel.region,
                        app.screen.region,
                    )
                    left = max(region.x for region in regions)
                    top = max(region.y for region in regions)
                    right = min(region.x + region.width for region in regions)
                    bottom = min(
                        region.y + region.height for region in regions
                    )
                    assert right > left and bottom > top
                    hit, _ = app.screen.get_widget_at(left, top)
                    assert hit is widget or widget in hit.ancestors

                live_scroll.focus()
                await pilot.press("pagedown")
                await wait_for_layout_to_settle(
                    pilot,
                    (live_scroll, status),
                    lambda: live_scroll.scroll_offset.y > 0,
                )
                assert_visible_hit(status)
                assert status.render().plain.strip()

                await pilot.press("pagedown")
                await wait_for_layout_to_settle(
                    pilot,
                    (live_scroll, events),
                    lambda: live_scroll.scroll_offset.y > 0
                    and events.option_count > 0,
                )
                assert_visible_hit(events)
                assert events.option_count > 0
                assert (switch.region, rebuild.region) == fixed_regions
            await _click_live_switch(pilot, panel.query_one("#live-switch", Switch))
            session = app._live_session
            await wait_for_ui_state(
                pilot,
                lambda: session.state == "paused" and not app._live_runner.running,
                "live pause should remain clickable in compact short geometry",
            )
            old = session
            await _click_live_panel_action(pilot, panel, "#live-rebuild")
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not old
                and app._live_session.state == "paused",
                "live rebuild should remain clickable in compact short geometry",
            )

    asyncio.run(run_check())


@pytest.mark.parametrize("size", [(80, 24), (80, 20), (160, 44)])
def test_tracking_summary_uses_click_time_cutoff_exports_and_survives_source_removal(  # noqa: PLR0915 - preserve cutoff, export, return, and captured event assertions
    tmp_path, monkeypatch, size
):
    pattern = (
        "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger "
        "[%X{traceId}] - %msg%n"
    )
    entries = [
        f"2026-11-01 12:00:00.00{index} [worker] INFO example.Worker "
        f"[Trace-{index}] - message-{index}\n"
        for index in range(1, 4)
    ]
    initial = "".join(entries).encode("utf-8")
    source = tmp_path / "app.log"
    source.write_bytes(initial)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    worker_started = threading.Event()
    worker_release = threading.Event()
    observed_cutoffs = []
    original_builder = live_controller.build_tracking_summary

    def block_summary_worker(snapshot, *, control=None):
        observed_cutoffs.append(snapshot.cutoff)
        worker_started.set()
        if not worker_release.wait(timeout=10):
            raise AssertionError("summary worker was not released by the UI test")
        return original_builder(snapshot, control=control)

    monkeypatch.setattr(live_controller, "build_tracking_summary", block_summary_worker)

    async def run_check():  # noqa: PLR0915 - keep the real summary and return workflow together
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await set_path_input_and_wait(
                pilot, app.query_one("#path", Input), str(source)
            )
            app.query_one("#manual_pattern_override").value = True
            app.query_one("#log_pattern", Input).value = pattern
            mode = app.query_one("#analysis-mode", Select)
            mode.value = "live"
            panel = app.query_one("#live-panel", LivePanel)
            await wait_for_ui_state(
                pilot,
                lambda: app._analysis_mode == "live" and panel.display,
                "live panel should be visible before starting",
            )

            switch = panel.query_one("#live-switch", Switch)
            await _click_live_switch(pilot, switch)
            await wait_for_ui_state(
                pilot,
                lambda: app._live_session is not None
                and app._live_session.completed >= 2,
                "initial complete entries should be captured",
            )
            session = app._live_session
            assert session.offset == len(initial)
            assert session.matched == 2
            assert session.has_pending

            summary_button = panel.query_one("#live-summary")
            summary_button.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#live-summary")
            assert await pilot.click("#live-summary")
            await wait_until(
                worker_started.is_set,
                "summary worker should receive the fixed snapshot",
                timeout=10,
            )
            assert observed_cutoffs == [2]

            appended = (
                "2026-11-01 12:00:00.004 [worker] INFO example.Worker "
                "[Trace-4] - message-4\n"
            ).encode("utf-8")
            with source.open("ab") as stream:
                stream.write(appended)
            await wait_for_ui_state(
                pilot,
                lambda: session.completed >= 3 and session.matched >= 3,
                "tracking should continue while its summary worker is running",
            )
            assert session.matched == 3
            source.unlink()

            try:
                worker_release.set()
                await wait_for_ui_state(
                    pilot,
                    lambda: app._live_summary_active
                    and app._live_summary_result is not None,
                    "summary should publish from the click-time snapshot",
                    timeout=10,
                )
            finally:
                worker_release.set()

            result = app._live_summary_result
            snapshot = result.tracking_snapshot
            assert snapshot.cutoff == 2
            assert snapshot.completed == 2
            assert result.total_logs == 2
            assert result.matched_occurrences == 2
            assert result.analysis_request == snapshot.conditions
            groups = list(result.matched_logs)
            occurrences = [
                occurrence
                for group in groups
                for occurrence in group["occurrences"]
            ]
            assert [item["sequence"] for item in occurrences] == [1, 2]
            assert [item["mdc"] for item in occurrences] == [
                {"traceId": "Trace-1"},
                {"traceId": "Trace-2"},
            ]
            assert all("app.log" == item["filename"] for item in occurrences)
            await pilot.resize_terminal(*size)
            metadata = app.query_one("#live-summary-metadata", Static).render().plain
            assert "資料來源：追蹤快照" in metadata
            assert "截止序號：2" in metadata

            result_box = app.query_one("#result-box")
            output_actions = app.query_one("#output-actions")
            export_button = app.query_one("#export-result")
            return_button = app.query_one("#return-live")
            await wait_for_layout_to_settle(
                pilot,
                (result_box, output_actions, export_button, return_button),
            )
            assert result_box.region.height > 0
            assert output_actions.region.height > 0
            for button in (export_button, return_button):
                assert button.region.height > 0
                assert button.region.y + button.region.height <= app.size.height
            assert (
                export_button.region.x + export_button.region.width
                <= return_button.region.x
                or return_button.region.x + return_button.region.width
                <= export_button.region.x
            )

            base_screen = app.screen
            await wait_for_button_ready(app, pilot, "#live-summary-details")
            assert await pilot.click("#live-summary-details")
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is not base_screen,
                "complete tracking conditions should open in a separate readable view",
            )
            details = app.screen.query_one(
                "#live-summary-details-content", Static
            ).render().plain
            for field in (
                "來源：",
                "擷取時間：",
                "截止序號：2",
                "已完成 Log：2；摘要命中：2",
                "尾端：",
                "缺口：",
                "條件：Level",
                "時間：",
                "Pattern：",
            ):
                assert field in details, f"{field!r} not found in details: {details!r}"
            await wait_for_button_ready(app, pilot, "#live-summary-details-close")
            assert await pilot.click("#live-summary-details-close")
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is base_screen,
                "closing summary details should preserve the summary page",
            )

            await wait_for_button_ready(app, pilot, "#export-result")
            assert await pilot.click("#export-result")
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#export-settings").display,
                "export settings should open for the captured tracking result",
            )
            assert app._last_result is result
            if size == (160, 44):
                await wait_for_export_confirmation_layout(app, pilot)
                app.query_one("#output_path", Input).value = str(tmp_path)
                app.query_one("#output_name", Input).value = "tracking-cutoff"
                app.query_one("#format", Select).value = "json"
                await wait_for_export_confirmation_layout(app, pilot)
                assert await pilot.click("#confirm-export")
                await wait_for_export_to_finish(
                    app,
                    pilot,
                    lambda: app.query_one("#export-status", Static)
                    .render()
                    .plain.startswith("匯出完成："),
                )
                report = json.loads(
                    (tmp_path / "tracking-cutoff.json").read_text(encoding="utf-8")
                )
                assert sum(
                    len(group["occurrences"]) for group in report["results"]
                ) == snapshot.cutoff
                assert [
                    occurrence["mdc"]["traceId"]
                    for group in report["results"]
                    for occurrence in group["occurrences"]
                ] == ["Trace-1", "Trace-2"]
                assert result.matched_occurrences == 2

            return_button = app.query_one("#return-live")
            return_button.scroll_visible(animate=False)
            await wait_for_button_ready(app, pilot, "#return-live")
            assert await pilot.click("#return-live")
            await wait_for_ui_state(
                pilot,
                lambda: not app._live_summary_active and panel.display,
                "return should restore the live panel without changing the session",
            )
            assert app._live_session is session
            assert session.state in {"tracking", "interrupted"}
            assert session.offset == len(initial) + len(appended)
            assert app.query_one("#form-column").display

            if size == (160, 44):
                option_list = panel.query_one("#live-events", OptionList)
                option_list.focus()
                await pilot.press("enter")
                await wait_for_ui_state(
                    pilot,
                    lambda: isinstance(app.screen, LiveEventScreen),
                    "saved event details should open after source removal",
                )
                event_screen = app.screen
                assert "message-3" in event_screen.query_one(
                    "#live-event-raw", Static
                ).render().plain
                assert "Trace-3" in event_screen.query_one(
                    "#live-event-mdc", Static
                ).render().plain
                assert "\\u" not in event_screen.query_one(
                    "#live-event-metadata", Static
                ).render().plain
                await pilot.press("escape")

    asyncio.run(run_check())
