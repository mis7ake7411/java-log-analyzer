from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from textual.widgets import Input, RichLog
import os

from tui_waits import (
    wait_for_layout_to_settle as _wait_for_layout_to_settle,
    wait_for_ui_state,
)

from log_analyzer.presentation.tui import app as tui


ACTION_LAYOUT_SIZES = (
    (160, 44),
    (120, 40),
    (88, 44),
    (80, 32),
    (80, 24),
)


def _assert_inside(widget, container) -> None:
    region = widget.region
    bounds = container.region
    assert region.x >= bounds.x
    assert region.y >= bounds.y
    assert region.x + region.width <= bounds.x + bounds.width
    assert region.y + region.height <= bounds.y + bounds.height


def _assert_form_actions_fit(app: tui.LogAnalyzerApp) -> None:
    content = app.query_one("#content")
    form_column = app.query_one("#form-column")
    form_panel = app.query_one("#form-panel")
    form_actions = app.query_one("#form-actions")
    manage_button = app.query_one("#manage-recipes")
    run_button = app.query_one("#run")
    viewport = app.screen

    assert form_actions.region.x == form_panel.region.x
    assert form_actions.region.x + form_actions.region.width == (
        form_panel.region.x + form_panel.region.width
    )
    assert form_actions.region.y >= form_panel.region.y + form_panel.region.height
    _assert_inside(form_actions, form_column)
    _assert_inside(form_actions, content)
    _assert_inside(form_actions, viewport)
    _assert_inside(manage_button, form_actions)
    _assert_inside(run_button, form_actions)
    assert manage_button.region.y == run_button.region.y
    assert run_button.region.x - manage_button.region.right >= 1
    assert run_button.region.width > 0


def _dashboard_result() -> SimpleNamespace:
    return SimpleNamespace(
        total_logs=738,
        matched_groups=285,
        matched_occurrences=738,
        exported_files=(),
        format_name="csv",
        input_path="C:/logs/" + "service/" * 30,
        output_path="C:/exports/report.csv",
        keyword="timeout " * 30,
        selected_levels=("DEBUG", "ERROR", "INFO", "WARN"),
        sort_by="time",
        ignore_case=False,
        level_summary=(("DEBUG", 602), ("ERROR", 5), ("INFO", 128), ("WARN", 3)),
        matched_logs=(),
        dashboard_summary=SimpleNamespace(
            exception_summary=(),
            time_hotspots=(),
            logger_summary=(),
            thread_summary=(),
        ),
    )


@pytest.mark.parametrize("size", ACTION_LAYOUT_SIZES, ids=['viewport-160x44', 'viewport-120x40', 'viewport-88x44', 'viewport-80x32', 'viewport-80x24'])
def test_form_actions_align_with_form_panel_and_stay_visible(size) -> None:
    """情境：不同終端尺寸下表單操作應對齊且保持可見。"""
    async def run_check() -> None:
        with (
            patch.object(tui, "load_recent_tui_state", return_value={}),
            patch.object(tui, "save_recent_tui_state"),
        ):
            app = tui.LogAnalyzerApp()
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                _assert_form_actions_fit(app)

                feedback = app.query_one("#recipe-feedback")
                feedback.update(
                    "已載入配方「very-long-recipe-"
                    + "x" * 64
                    + "」的條件；尚未執行分析。"
                )
                feedback.display = True
                await _wait_for_layout_to_settle(
                    pilot,
                    (
                        app.query_one("#form-panel"),
                        app.query_one("#form-actions"),
                        app.query_one("#manage-recipes"),
                        app.query_one("#run"),
                        feedback,
                    ),
                    lambda: feedback.region.width > 0 and feedback.region.height > 0,
                )

                _assert_form_actions_fit(app)
                actions = app.query_one("#form-actions")
                assert feedback.region.y >= actions.region.y + actions.region.height
                assert feedback.region.height >= 2
                _assert_inside(feedback, app.query_one("#content"))
                _assert_inside(feedback, app.screen)

    asyncio.run(run_check())


@pytest.mark.parametrize("size", [(72, 28), (80, 40), (160, 40)], ids=['viewport-72x28', 'viewport-80x40', 'viewport-160x40'])
def test_result_remains_readable_with_changed_conditions_in_short_window(size):
    """情境：短視窗中修改條件後結果仍應可閱讀與捲動。"""
    async def run_check():
        with (
            patch.object(tui, "load_recent_tui_state", return_value={}),
            patch.object(tui, "save_recent_tui_state"),
        ):
            app = tui.LogAnalyzerApp()
            async with app.run_test(size=size) as pilot:
                box = app.query_one("#result-box", RichLog)
                app._pending_result_identity = app._capture_result_form_identity()
                app._display_analysis_result(box, _dashboard_result())
                notice = app.query_one("#result-context-notice")
                app.query_one("#keyword", Input).value = "changed"
                await wait_for_ui_state(pilot, lambda: notice.display, "changed conditions notice did not appear")
                await _wait_for_layout_to_settle(pilot, (box, notice))
                assert notice.display
                assert box.content_size.height >= 6
                _assert_form_actions_fit(app)
                for button in app.query_one("#output-actions").query("Button"):
                    _assert_inside(button, app.screen)
                assert box.max_scroll_y > 0
                box.scroll_end(animate=False)
                await wait_for_ui_state(pilot, lambda: box.scroll_y > 0, "result scroll did not move")
                capture = os.environ.get("TUI_CAPTURE_DIR")
                if capture:
                    app.save_screenshot(filename=f"readable-result-{size[0]}x{size[1]}.svg", path=capture)

    asyncio.run(run_check())


def test_compact_run_and_recipe_buttons_remain_clickable(tmp_path, monkeypatch) -> None:
    """情境：窄版配方與分析按鈕應實際可點擊。"""
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        with (
            patch.object(tui, "load_recent_tui_state", return_value={}),
            patch.object(tui, "save_recent_tui_state"),
        ):
            app = tui.LogAnalyzerApp()
            run_calls: list[str] = []

            def capture_run() -> None:
                run_calls.append("run")

            app._schedule_analysis_start = capture_run
            async with app.run_test(size=(80, 32)) as pilot:
                await pilot.pause()
                await pilot.click("#run")
                await pilot.pause()
                assert run_calls == ["run"]

                await pilot.click("#manage-recipes")
                await pilot.pause()
                assert len(app.screen.query("#recipe-dialog")) == 1

    asyncio.run(run_check())


def test_completed_dashboard_reflows_with_viewport_width() -> None:
    """情境：已完成儀表板應隨可用寬度重新排列。"""
    async def run_check() -> None:
        with (
            patch.object(tui, "load_recent_tui_state", return_value={}),
            patch.object(tui, "save_recent_tui_state"),
        ):
            app = tui.LogAnalyzerApp()
            async with app.run_test(size=(240, 44)) as pilot:
                await pilot.pause()
                app._display_analysis_result(
                    app.query_one("#result-box", RichLog),
                    _dashboard_result(),
                )
                await pilot.pause()

                for width in (240, 88, 80, 160):
                    await pilot.resize_terminal(width, 44)
                    await pilot.pause()
                    await pilot.pause()

                    content = app.query_one("#content")
                    output_panel = app.query_one("#output-panel")
                    result_box = app.query_one("#result-box", RichLog)
                    output_actions = app.query_one("#output-actions")

                    _assert_inside(output_panel, content)
                    _assert_inside(output_panel, app.screen)
                    _assert_inside(result_box, output_panel)
                    _assert_inside(output_actions, output_panel)
                    for button in output_actions.query("Button"):
                        _assert_inside(button, output_actions)
                        _assert_inside(button, app.screen)
                    content_width = result_box.scrollable_content_region.width
                    rendered_line_widths = [
                        sum(segment.cell_length for segment in line)
                        for line in result_box.lines
                    ]
                    assert max(rendered_line_widths, default=0) <= content_width
                    assert result_box.virtual_size.width <= content_width

    asyncio.run(run_check())
