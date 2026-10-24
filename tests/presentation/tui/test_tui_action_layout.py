from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from textual.widgets import RichLog

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


async def _wait_for_layout_to_settle(pilot, widgets, ready) -> None:
    previous_layout = None
    for _ in range(10):
        await pilot.pause()
        if not ready():
            previous_layout = None
            continue
        layout = tuple(
            (
                widget.region.x,
                widget.region.y,
                widget.region.width,
                widget.region.height,
                widget.display,
                tuple(sorted(widget.classes)),
                getattr(widget, "content_size", None),
            )
            for widget in widgets
        )
        if layout == previous_layout:
            return
        previous_layout = layout
    raise AssertionError("form action layout did not settle")


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


@pytest.mark.parametrize("size", ACTION_LAYOUT_SIZES)
def test_form_actions_align_with_form_panel_and_stay_visible(size) -> None:
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


def test_compact_run_and_recipe_buttons_remain_clickable(tmp_path, monkeypatch) -> None:
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
