import asyncio
import json
import os

import pytest
from rich.cells import cell_len
from textual.widgets import Input, OptionList, Static

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui.controllers import results as results_module
from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.screens.event_browser import EventBrowserScreen
from log_analyzer.application.analysis_service import ResultExportOptions, export_analysis_result
from tui_waits import (
    wait_for_button_ready,
    wait_for_scheduled_analysis,
    wait_for_ui_state,
    wait_for_layout_to_settle,
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    """隔離表單並建立不同層級、含相似訊息的分析來源。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda state: None)
    lines = []
    for level, message, count in (
        ("ERROR", "failed task [bold] literal", 5),
        ("INFO", "ERROR is only message text", 3),
        ("WARN", "failed task again", 1),
        ("ERROR", "another failure", 2),
    ):
        lines.extend(
            f"2026-10-24 10:00:00.000 [main] {level} Service - {message}\n"
            for _ in range(count)
        )
    (tmp_path / "app.log").write_text("".join(lines), encoding="utf-8")
    return tmp_path


async def _analyze(app, pilot):
    await wait_for_button_ready(app, pilot)
    app.query_one("#run").focus()
    await pilot.press("enter")
    await wait_for_scheduled_analysis(app)
    assert app._last_result is not None
    return app._last_result


async def _wait_for_browser(app, pilot):
    def ready():
        screen = app.screen
        return (
            isinstance(screen, EventBrowserScreen)
            and screen.is_mounted
            and bool(screen.query("#event-options"))
            and not screen._querying
            and (screen._query_task is None or screen._query_task.done())
        )

    await wait_for_ui_state(pilot, ready, "event browser did not finish mounting and filtering")
    return app.screen


def test_keyboard_summary_navigation_opens_exact_level_without_reanalysis(source):
    """情境：摘要導覽選 ERROR，只縮小瀏覽範圍且不改寫原請求或結果。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            result = await _analyze(app, pilot)
            original_request = result.analysis_request
            original_task = app._scheduled_analysis_task
            app.set_focus(None)
            await pilot.press("d")
            await wait_for_ui_state(
                pilot,
                lambda: bool(app.screen.query("#summary-options")),
                "summary navigation not opened",
            )
            options = app.screen.query_one("#summary-options", OptionList)
            error_index = next(
                index
                for index, target in enumerate(app._dashboard_targets)
                if target.level == "ERROR"
            )
            options.highlighted = error_index
            await pilot.press("enter")
            await _wait_for_browser(app, pilot)
            assert app.screen._group_page
            assert all(group["level"] == "ERROR" for group in app.screen._group_page)
            assert app._last_result is result
            assert result.analysis_request is original_request
            assert app._scheduled_analysis_task is original_task
            output = source / "dashboard-export.json"
            await asyncio.to_thread(export_analysis_result, result, ResultExportOptions(str(output), fmt="json"))
            payload = json.loads(output.read_text(encoding="utf-8"))
            assert len(payload["results"]) == result.matched_groups
            assert set(payload["summary"]) == {"ERROR", "INFO", "WARN"}
            assert sum(row["count"] for row in payload["results"]) == result.matched_occurrences

    asyncio.run(check())


def _link_cell(log, action, *, bar_only=False):
    for y, line in enumerate(log.lines):
        x = 0
        for segment in line:
            if (
                segment.style
                and segment.style.meta.get("@click") == action
                and (not bar_only or "█" in segment.text or "░" in segment.text)
            ):
                return x, y
            x += cell_len(segment.text)
    return None


@pytest.mark.parametrize(
    "kind,size",
    [
        ("level", (120, 45)),
        ("event", (120, 45)),
        ("level", (80, 40)),
        ("event", (80, 40)),
        ("bar", (80, 40)),
    ],
    ids=[
        "wide-level-click",
        "wide-event-click",
        "narrow-level-click",
        "narrow-event-click",
        "narrow-bar-click",
    ],
)
def test_click_chart_or_top_event_opens_exact_retained_groups(source, kind, size):
    """情境：點擊實際繪製的 Level 或高頻事件會開啟正確群組，不重新分析。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            result = await _analyze(app, pilot)
            form = app._capture_recipe_state()
            index = next(
                index
                for index, target in enumerate(app._dashboard_targets)
                if (
                    target.level == "ERROR"
                    if kind != "event"
                    else target.group_index is not None
                )
            )
            target = app._dashboard_targets[index]
            action = f"app.open_dashboard_target({app._dashboard_navigation_generation}, {index})"
            log = app.query_one("#result-box")
            await wait_for_ui_state(
                pilot,
                lambda: _link_cell(log, action, bar_only=kind == "bar") is not None,
                "clickable dashboard not rendered",
            )
            # 覆蓋 resize 排入下一輪 refresh、尚未重繪完成的順序。
            app.call_after_refresh(app._refresh_dashboard_after_resize)
            await wait_for_layout_to_settle(pilot, (log,))
            x, y = _link_cell(log, action, bar_only=kind == "bar")
            log.scroll_to(y=y, immediate=True)
            await wait_for_layout_to_settle(pilot, (log,))
            x, y = _link_cell(log, action, bar_only=kind == "bar")
            offset = (
                log.content_region.x - log.region.x + x,
                log.content_region.y - log.region.y + y - int(log.scroll_y),
            )
            assert 0 <= offset[1] < log.region.height
            capture = os.environ.get("TUI_CAPTURE_DIR")
            if capture:
                app.save_screenshot(
                    filename=f"dashboard-{kind}-{size[0]}.svg", path=capture
                )
            assert await pilot.click(log, offset=offset)
            await _wait_for_browser(app, pilot)
            if kind != "event":
                assert app.screen._group_page
                assert all(
                    group["level"] == "ERROR" for group in app.screen._group_page
                )
            else:
                assert app.screen._group_indices == [target.group_index]
                assert len(app.screen._group_page) == 1
                assert (
                    app.screen._group_page[0] == result.matched_logs[target.group_index]
                )
            assert app._capture_recipe_state() == form
            assert app._last_result is result

    asyncio.run(check())


def test_scoped_browser_search_intersects_scope_and_clear_restores_all_groups(source):
    """情境：摘要範圍內搜尋不會混入其他 Level，清除才恢復所有群組。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            result = await _analyze(app, pilot)
            screen = EventBrowserScreen(result, page_size=1, initial_level="ERROR")
            app.push_screen(screen)
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying and screen._group_indices is not None,
                "initial scope not ready",
            )
            assert (
                "Level：ERROR"
                in screen.query_one("#browser-query-status", Static).render().plain
            )
            assert screen._group_length() == 2
            assert all(
                result.matched_logs[index]["level"] == "ERROR"
                for index in screen._group_indices
            )
            screen._move_page(1)
            assert screen._group_start == 1
            assert screen._group_page[0]["level"] == "ERROR"
            screen._move_page(-1)
            screen.query_one("#browser-query", Input).value = "only message text"
            screen._start_query()
            await wait_for_ui_state(
                pilot, lambda: not screen._querying, "scoped search not ready"
            )
            assert screen._group_indices == []
            screen.query_one("#browser-clear").focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: not screen._querying and screen._group_indices is None,
                "scope was not cleared",
            )
            assert screen._scope_level is None
            assert screen._scope_group_index is None
            assert "Level：" not in screen.query_one("#browser-query-status", Static).render().plain
            assert screen._group_length() == len(result.matched_logs)
            screen._move_page(1)
            assert screen._group_start == 1
            assert len(screen._group_page) == 1

    asyncio.run(check())


def test_stale_dashboard_target_does_not_open_released_result(source):
    """情境：清除分析後，舊圖表的入口與摘要導覽皆失效。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            await _analyze(app, pilot)
            generation = app._dashboard_navigation_generation
            screen = app.screen
            app.action_clear_result()
            app.action_open_dashboard_target(generation, 0)
            app.action_browse_summary()
            assert app.screen is screen
            assert app._last_result is None

    asyncio.run(check())


@pytest.mark.parametrize(
    "size",
    [(160, 45), (120, 40), (80, 40), (70, 24)],
    ids=["wide", "split-panel", "narrow", "short"],
)
def test_summary_keyboard_navigation_fits_viewport_and_can_be_cancelled(source, size):
    """情境：寬窄版摘要導覽可捲動長訊息，取消後仍保留分析結果。"""
    (source / "app.log").write_text(
        "2026-10-24 10:00:00.000 [main] ERROR Service - "
        + "長訊息 [bold] / " * 80
        + "\n",
        encoding="utf-8",
    )

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            result = await _analyze(app, pilot)
            app.set_focus(None)
            await pilot.press("d")
            screen = app.screen
            options = screen.query_one("#summary-options", OptionList)
            close = screen.query_one("#close-summary-navigation")
            await wait_for_layout_to_settle(pilot, (options, close))
            assert screen.region.contains_region(close.region)
            assert screen.region.contains_region(options.region)
            assert options.has_focus
            assert options.max_scroll_x == 0
            capture = os.environ.get("TUI_CAPTURE_DIR")
            if capture:
                app.save_screenshot(
                    filename=f"summary-navigation-{size[0]}x{size[1]}.svg", path=capture
                )
            await pilot.press("escape")
            assert app.screen is not screen
            assert app._last_result is result

    asyncio.run(check())


def test_summary_shortcut_does_not_stack_over_an_event_browser(source):
    """情境：事件瀏覽器中的 d 不會再次疊上摘要導覽。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            await _analyze(app, pilot)
            app.action_browse_events()
            screen = await _wait_for_browser(app, pilot)
            await wait_for_layout_to_settle(
                pilot, (screen.query_one("#event-options"),)
            )
            app.set_focus(None)
            await pilot.press("d")
            assert app.screen is screen

    asyncio.run(check())


def test_navigation_cache_is_not_published_until_targets_are_ready(source, monkeypatch):
    """情境：建立入口失敗不發布半成品快取，恢復後仍可重新建立。"""
    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            result = await _analyze(app, pilot)
            app._dashboard_target_result = None
            generation = app._dashboard_navigation_generation
            original = results_module.build_dashboard_targets
            def fail(*args):
                raise OSError("cannot read summary")
            monkeypatch.setattr(results_module, "build_dashboard_targets", fail)
            with pytest.raises(OSError, match="cannot read summary"):
                app._prepare_dashboard_navigation(result)
            assert app._dashboard_target_result is None
            assert app._dashboard_navigation_generation == generation
            monkeypatch.setattr(results_module, "build_dashboard_targets", original)
            assert app._prepare_dashboard_navigation(result) == generation + 1
            assert app._dashboard_target_result is result
    asyncio.run(check())
