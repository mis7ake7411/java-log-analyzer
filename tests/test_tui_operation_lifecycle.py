import asyncio
import threading
from pathlib import Path

import pytest
from textual.widgets import Input

from log_analyzer.application.analysis_service import AnalysisRequest, analyze_logs
from log_analyzer.presentation import tui as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp


async def _wait_until(predicate) -> None:
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("operation did not reach the expected lifecycle state")


def _result_fixture(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-10-22 12:00:00.000 [main] ERROR com.test.App - retained during shutdown\n",
        encoding="utf-8",
    )
    return analyze_logs(AnalysisRequest(path=str(logs)))


def test_shutdown_defers_store_release_until_export_finishes(tmp_path, monkeypatch):
    result = _result_fixture(tmp_path)
    store_path = Path(result.matched_logs.path)
    entered = threading.Event()
    finish = threading.Event()
    real_export = tui_module.export_analysis_result

    def delayed_export(retained_result, options):
        entered.set()
        if not finish.wait(10):
            raise TimeoutError("test did not release export")
        return real_export(retained_result, options)

    monkeypatch.setattr(tui_module, "export_analysis_result", delayed_export)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        operation = None
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app._last_result = result
                app._set_result_status("complete", result)
                app.query_one("#output_path").value = str(tmp_path)
                app.query_one("#output_name").value = "shutdown"
                operation = asyncio.create_task(app.action_confirm_export())
                await _wait_until(entered.is_set)
            assert app._release_result_when_operation_finishes
            assert store_path.exists(), "shutdown must not delete data being exported"
        finally:
            finish.set()
            if operation is not None:
                await asyncio.wait_for(operation, timeout=5)
        assert (tmp_path / "shutdown.csv").is_file()
        assert not store_path.exists()
        assert app._last_result is None

    asyncio.run(run_check())


def test_shutdown_releases_analysis_that_finishes_after_unmount(tmp_path, monkeypatch):
    result = _result_fixture(tmp_path)
    store_path = Path(result.matched_logs.path)
    entered = threading.Event()
    finish = threading.Event()

    def delayed_analysis(_request):
        entered.set()
        if not finish.wait(10):
            raise TimeoutError("test did not release analysis")
        return result

    monkeypatch.setattr(tui_module, "analyze_logs", delayed_analysis)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        operation = None
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#path").value = result.input_path
                operation = asyncio.create_task(app.action_run_analysis())
                await _wait_until(entered.is_set)
            assert app._release_result_when_operation_finishes
            assert store_path.exists()
        finally:
            finish.set()
            if operation is not None:
                await asyncio.wait_for(operation, timeout=5)
        assert not store_path.exists()
        assert app._last_result is None

    asyncio.run(run_check())


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_shutdown_does_not_render_late_analysis_errors(tmp_path, monkeypatch, error_type):
    entered = threading.Event()
    finish = threading.Event()

    def failing_analysis(_request):
        entered.set()
        if not finish.wait(10):
            raise TimeoutError("test did not release analysis")
        raise error_type("analysis failed after the interface closed")

    monkeypatch.setattr(tui_module, "analyze_logs", failing_analysis)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check():
        app = LogAnalyzerApp()
        operation = None
        try:
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one("#path").value = str(tmp_path)
                operation = asyncio.create_task(app.action_run_analysis())
                await _wait_until(entered.is_set)
            assert app._release_result_when_operation_finishes
        finally:
            finish.set()
            if operation is not None:
                await asyncio.wait_for(operation, timeout=5)
        assert app._last_result is None

    asyncio.run(run_check())


@pytest.mark.parametrize("field_id", ["path", "output_path"])
def test_shutdown_ignores_queued_path_input_changes(monkeypatch, field_id):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            path_input = app.query_one(f"#{field_id}", Input)
        assert not app.is_running
        app.on_input_changed(Input.Changed(path_input, "late path update"))

    asyncio.run(run_check())
