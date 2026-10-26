import asyncio
import os
from pathlib import Path

import pytest

from textual.widgets import Checkbox, Input, Static

from tui_waits import wait_for_button_ready, wait_for_scheduled_analysis, wait_for_ui_state

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui.app import LogAnalyzerApp


@pytest.mark.parametrize("size", [(80, 40), (160, 45)], ids=['viewport-80x40', 'viewport-160x45'])
def test_result_notice_tracks_form_changes_but_export_uses_frozen_request(tmp_path, monkeypatch, size):
    """情境：寬窄版修改表單應更新條件提示但匯出仍用固定請求。"""
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    (tmp_path / "app.log").write_text("2026-10-24 10:00:00.000 [main] INFO Service - original\n", encoding="utf-8")

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            app.query_one("#path", Input).value = str(tmp_path)
            await wait_for_button_ready(app, pilot)
            app.query_one("#run").focus()
            await pilot.press("enter")
            await wait_for_scheduled_analysis(app)
            result = app._last_result
            notice = app.query_one("#result-context-notice", Static)
            assert not notice.display
            app.query_one("#keyword", Input).value = "new condition"
            await wait_for_ui_state(pilot, lambda: notice.display, "changed form was not marked")
            assert "尚未重新分析" in notice.render().plain
            capture = os.environ.get("TUI_CAPTURE_DIR")
            if capture:
                Path(capture).mkdir(parents=True, exist_ok=True)
                app.save_screenshot(filename=f"result-context-{size[0]}.svg", path=capture)
            app.action_show_export_settings()
            await wait_for_ui_state(pilot, lambda: app.query_one("#export-result-context", Static).render().plain != "", "export context did not appear")
            context = app.query_one("#export-result-context", Static).render().plain
            assert "原條件" in context
            assert "new condition" not in context
            assert str(tmp_path.resolve()) in context
            assert result.analysis_request.keyword is None
            app.query_one("#keyword", Input).value = ""
            await wait_for_ui_state(pilot, lambda: not notice.display, "restored form stayed marked")
            app.query_one("#output_name", Input).value = "another-report"
            app.query_one("#export_field_thread", Checkbox).value = False
            await pilot.pause()
            assert not notice.display
            app.action_clear_result()
            assert not notice.display

    asyncio.run(check())


def test_edits_during_analysis_and_failed_retry_keep_previous_result_identity(tmp_path, monkeypatch):
    """情境：分析途中編輯及失敗重試應保留上次結果的條件識別。"""
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    (tmp_path / "app.log").write_text("2026-10-24 10:00:00.000 [main] INFO Service - original\n", encoding="utf-8")

    async def check():
        app = LogAnalyzerApp()
        entered, release = asyncio.Event(), asyncio.Event()
        async with app.run_test(size=(120, 42)) as pilot:
            original = app._analyze_request

            async def gated(request):
                entered.set()
                await release.wait()
                return await original(request)

            monkeypatch.setattr(app, "_analyze_request", gated)
            app.query_one("#path", Input).value = str(tmp_path)
            await wait_for_button_ready(app, pilot)
            app.query_one("#run").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, entered.is_set, "analysis did not start")
            app.query_one("#keyword", Input).value = "no match"
            release.set()
            await wait_for_scheduled_analysis(app)
            result = app._last_result
            notice = app.query_one("#result-context-notice", Static)
            assert result.analysis_request.keyword is None
            await wait_for_ui_state(pilot, lambda: notice.display, "edits made during analysis were lost")
            monkeypatch.setattr(app, "_analyze_request", original)
            previous_task = app._scheduled_analysis_task
            await wait_for_button_ready(app, pilot)
            app.query_one("#run").focus()
            await pilot.press("enter")
            await wait_for_scheduled_analysis(app, previous_task)
            assert app._last_result is result
            assert notice.display
            assert "上次" in app.query_one("#result-status", Static).render().plain
            app.query_one("#keyword", Input).value = ""
            await wait_for_ui_state(pilot, lambda: not notice.display, "reverting failed retry settings did not clear notice")
            app.query_one("#log_pattern", Input).value = "unused manual text"
            await pilot.pause()
            assert not notice.display

    asyncio.run(check())
