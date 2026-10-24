from __future__ import annotations

import asyncio
import threading
import time
from importlib import import_module, util
from pathlib import Path

import pytest

from tui_waits import (
    wait_until as _wait_until,
)

from log_analyzer.application.analysis_service import AnalysisRequest, NoMatchingLogsError, analyze_logs
from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp


def _analysis_control_module():
    module_name = "log_analyzer.domain.analysis_control"
    assert util.find_spec(module_name) is not None, "analysis control types should exist"
    return import_module(module_name)


def _result_fixture(tmp_path: Path, folder: str, message: str):
    logs = tmp_path / folder
    logs.mkdir()
    (logs / "app.log").write_text(
        f"2026-10-24 12:00:00.000 [main] ERROR com.test.App - {message}\n",
        encoding="utf-8",
    )
    return analyze_logs(AnalysisRequest(path=str(logs)))


def _assert_region_inside(child, parent) -> None:
    child_region = child.region
    parent_region = parent.region
    assert child_region.x >= parent_region.x
    assert child_region.x + child_region.width <= parent_region.x + parent_region.width
    assert child_region.y >= parent_region.y
    assert child_region.y + child_region.height <= parent_region.y + parent_region.height


def _patch_recent_state(monkeypatch) -> None:
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)


def test_failed_reanalysis_keeps_previous_store_bytes_and_exportability(tmp_path, monkeypatch):
    previous_result = _result_fixture(tmp_path, "previous", "retain me")
    previous_store_path = Path(previous_result.matched_logs.path)
    previous_store_bytes = previous_store_path.read_bytes()
    _patch_recent_state(monkeypatch)

    def fail_analysis(_request, *, control=None):
        raise ValueError("deliberate analysis failure")

    monkeypatch.setattr(tui_module, "analyze_logs", fail_analysis)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._display_analysis_result(app.query_one("#result-box"), previous_result)
            app.query_one("#path").value = previous_result.input_path

            await app.action_run_analysis()

            assert app._last_result is previous_result
            assert previous_store_path.exists()
            assert previous_store_path.read_bytes() == previous_store_bytes
            assert app.query_one("#export-result").disabled is False
            assert "保留" in str(app.query_one("#result-status").render())

    asyncio.run(run_check())


def test_no_matching_reanalysis_keeps_previous_result_and_exportability(tmp_path, monkeypatch):
    previous_result = _result_fixture(tmp_path, "previous", "retain on no matches")
    previous_store_path = Path(previous_result.matched_logs.path)
    previous_store_bytes = previous_store_path.read_bytes()
    empty_logs = tmp_path / "empty-logs"
    empty_logs.mkdir()
    _patch_recent_state(monkeypatch)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._display_analysis_result(app.query_one("#result-box"), previous_result)
            previous_status = str(app.query_one("#result-status").render())
            app.query_one("#path").value = str(empty_logs)

            await app.action_run_analysis()

            assert app._last_result is previous_result
            assert previous_store_path.exists()
            assert previous_store_path.read_bytes() == previous_store_bytes
            assert app._result_state == "complete"
            assert str(app.query_one("#result-status").render()) != previous_status
            assert app.query_one("#export-result").disabled is False

    asyncio.run(run_check())


def test_no_matching_reanalysis_shows_encoding_warning_and_keeps_previous_result(tmp_path, monkeypatch):
    previous_result = _result_fixture(tmp_path, "previous", "retain with encoding warning")
    previous_store_path = Path(previous_result.matched_logs.path)
    previous_store_bytes = previous_store_path.read_bytes()
    unreadable_logs = tmp_path / "unreadable-logs"
    unreadable_logs.mkdir()
    (unreadable_logs / "unmatched.log").write_bytes(b"\xff not a log entry\n")
    _patch_recent_state(monkeypatch)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._display_analysis_result(app.query_one("#result-box"), previous_result)
            app.query_one("#path").value = str(unreadable_logs)

            await app.action_run_analysis()

            result_box = app.query_one("#result-box")
            result_text = "".join(segment.text for line in result_box.lines for segment in line)
            visible_text = "".join(
                segment.text
                for line in result_box.lines[: result_box.region.height]
                for segment in line
            )
            status_text = str(app.query_one("#result-status").render())
            assert app._last_result is previous_result
            assert previous_store_path.exists()
            assert previous_store_path.read_bytes() == previous_store_bytes
            assert app._result_state == "complete"
            assert app._last_result.input_path == previous_result.input_path
            assert app.query_one("#export-result").disabled is False
            assert "UTF-8 編碼提醒" in result_text
            assert "UTF-8 編碼提醒" in visible_text
            assert "1 個檔案、1 行" in result_text
            assert "�" in result_text
            assert "分析完成" in result_text
            assert result_box.scroll_y == 0
            assert "沒有符合條件" in status_text
            assert "UTF-8 編碼提醒" not in status_text
            assert app.query_one("#result-status").region.height <= 2

            await pilot.resize_terminal(110, 44)
            await pilot.pause()
            resized_box = app.query_one("#result-box")
            resized_text = "".join(segment.text for line in resized_box.lines for segment in line)
            assert "UTF-8 編碼提醒" in resized_text
            assert "分析完成" in resized_text

    asyncio.run(run_check())


def test_restored_encoding_warning_clears_on_success_and_clear(tmp_path, monkeypatch):
    previous_result = _result_fixture(tmp_path, "previous", "replace after warning")
    replacement_result = _result_fixture(tmp_path, "replacement", "clean replacement")
    _patch_recent_state(monkeypatch)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            result_box = app.query_one("#result-box")
            diagnostics = ScanDiagnostics(encoding_error_files=1, encoding_error_lines=2)

            app._display_analysis_result(result_box, previous_result)
            app._handle_analysis_error(result_box, NoMatchingLogsError(diagnostics))
            assert app._restored_encoding_warning is not None

            app._display_analysis_result(result_box, replacement_result)
            assert app._restored_encoding_warning is None
            await pilot.resize_terminal(110, 44)
            await pilot.pause()
            result_text = "".join(segment.text for line in result_box.lines for segment in line)
            assert "本次掃描編碼提醒" not in result_text
            assert "分析完成" in result_text

            app._handle_analysis_error(result_box, NoMatchingLogsError(diagnostics))
            assert app._restored_encoding_warning is not None
            app.action_clear_result()
            assert app._restored_encoding_warning is None
            cleared_text = "".join(segment.text for line in result_box.lines for segment in line)
            assert "本次掃描編碼提醒" not in cleared_text

    asyncio.run(run_check())


def test_reanalysis_keeps_previous_store_alive_until_success_replaces_it(tmp_path, monkeypatch):
    previous_result = _result_fixture(tmp_path, "previous", "old dashboard")
    new_result = _result_fixture(tmp_path, "new", "new dashboard")
    previous_store_path = Path(previous_result.matched_logs.path)
    entered = threading.Event()
    finish = threading.Event()
    _patch_recent_state(monkeypatch)

    def delayed_analysis(_request, *, control=None):
        entered.set()
        if not finish.wait(5):
            raise TimeoutError("test did not finish analysis")
        return new_result

    monkeypatch.setattr(tui_module, "analyze_logs", delayed_analysis)

    async def run_check():
        app = LogAnalyzerApp()
        operation = None
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._display_analysis_result(app.query_one("#result-box"), previous_result)
                app.query_one("#path").value = new_result.input_path
                operation = asyncio.create_task(app.action_run_analysis())
                await _wait_until(entered.is_set)
                assert previous_store_path.exists()
                finish.set()
                await asyncio.wait_for(operation, timeout=5)
                assert app._last_result is new_result
                assert not previous_store_path.exists()
        finally:
            finish.set()

    asyncio.run(run_check())


@pytest.mark.parametrize(
    ("start_method", "cancel_method"),
    [("button", "button"), ("keyboard", "shortcut")],
)
def test_pilot_can_cancel_a_background_analysis_without_blocking_the_event_pump(
    tmp_path, monkeypatch, start_method, cancel_method
):
    control_module = _analysis_control_module()
    previous_result = _result_fixture(tmp_path, "previous", "keep after cancel")
    previous_store_path = Path(previous_result.matched_logs.path)
    entered = threading.Event()
    controls = []
    _patch_recent_state(monkeypatch)

    def wait_for_cancellation(_request, *, control):
        controls.append(control)
        entered.set()
        while True:
            control.raise_if_cancelled()
            time.sleep(0.005)

    monkeypatch.setattr(tui_module, "analyze_logs", wait_for_cancellation)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app._display_analysis_result(app.query_one("#result-box"), previous_result)
            app.query_one("#path").value = previous_result.input_path
            cancel_buttons = list(app.query("#cancel-analysis"))
            assert cancel_buttons, "a cancellation button should be available in the TUI"

            if start_method == "button":
                await asyncio.wait_for(pilot.click("#run"), timeout=1)
            else:
                await asyncio.wait_for(pilot.press("enter"), timeout=1)
            await _wait_until(entered.is_set)

            cancel_button = app.query_one("#cancel-analysis")
            assert cancel_button.display
            for size in ((80, 24), (160, 44)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                assert cancel_button.region.width > 0
                _assert_region_inside(cancel_button, app.screen)
                _assert_region_inside(cancel_button, app.query_one("#form-actions"))

            if cancel_method == "button":
                await asyncio.wait_for(pilot.click(cancel_button), timeout=1)
            else:
                await asyncio.wait_for(pilot.press("ctrl+x"), timeout=1)
            await _wait_until(lambda: not app._operation_in_progress)

            assert controls and isinstance(controls[0], control_module.AnalysisControl)
            assert controls[0].is_cancelled
            assert app._last_result is previous_result
            assert previous_store_path.exists()
            assert "取消" in str(app.query_one("#result-status").render())
            assert app.query_one("#export-result").disabled is False

    asyncio.run(run_check())


def test_queued_progress_after_cancel_does_not_overwrite_cancelling_status(tmp_path, monkeypatch):
    control_module = _analysis_control_module()
    _patch_recent_state(monkeypatch)
    entered = threading.Event()
    controls = []

    def wait_for_cancellation(_request, *, control):
        controls.append(control)
        entered.set()
        while not control.is_cancelled:
            time.sleep(0.005)
        control.raise_if_cancelled()

    monkeypatch.setattr(tui_module, "analyze_logs", wait_for_cancellation)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(tmp_path)
            await asyncio.wait_for(pilot.click("#run"), timeout=1)
            await _wait_until(entered.is_set)

            operation_id = app._active_analysis_operation_id
            app.action_cancel_analysis()
            cancelling_status = str(app.query_one("#result-status").render())
            app._apply_analysis_progress(
                operation_id,
                control_module.AnalysisProgress("scanning", "late.log", 0, 1, 128),
            )

            assert str(app.query_one("#result-status").render()) == cancelling_status
            await _wait_until(lambda: not app._operation_in_progress)

    asyncio.run(run_check())


def test_late_progress_callback_from_completed_operation_is_ignored(tmp_path, monkeypatch):
    control_module = _analysis_control_module()
    result = _result_fixture(tmp_path, "logs", "completed result")
    controls = []
    _patch_recent_state(monkeypatch)

    def successful_analysis(_request, *, control):
        controls.append(control)
        return result

    monkeypatch.setattr(tui_module, "analyze_logs", successful_analysis)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path").value = result.input_path
            await asyncio.wait_for(pilot.click("#run"), timeout=1)
            await _wait_until(lambda: not app._operation_in_progress)
            previous_status = str(app.query_one("#result-status").render())

            controls[0].report_progress(
                "scanning",
                filename="stale.log",
                files_total=9,
                lines_processed=9999,
                force=True,
            )
            await pilot.pause()

            assert isinstance(controls[0], control_module.AnalysisControl)
            assert str(app.query_one("#result-status").render()) == previous_status
            assert app._last_result is result

    asyncio.run(run_check())
