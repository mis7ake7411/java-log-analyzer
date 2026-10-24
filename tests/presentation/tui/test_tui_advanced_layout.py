import asyncio
from itertools import combinations

import pytest

from tui_waits import (
    wait_for_ui_state as _wait_for_ui_state,
    wait_for_layout_to_settle as _wait_for_layout_to_settle,
)

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp


SIZES = ((80, 24), (80, 32), (100, 40), (120, 40), (160, 44), (240, 44))
ADVANCED_CONTROL_IDS = (
    "logback_xml_path",
    "browse_logback_xml",
    "manual_pattern_override",
    "log_pattern",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
    "ignore_case",
    "sort_by",
    "level_ERROR",
    "level_WARN",
    "level_INFO",
    "level_DEBUG",
    "level_TRACE",
    "level_unrestricted",
    "select_all_levels",
    "clear_levels",
)


def _disable_recent_state(monkeypatch):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)


def _overlap(first, second) -> bool:
    return (
        first.x < second.x + second.width
        and second.x < first.x + first.width
        and first.y < second.y + second.height
        and second.y < first.y + first.height
    )


@pytest.mark.parametrize("size", SIZES, ids=['viewport-80x24', 'viewport-80x32', 'viewport-100x40', 'viewport-120x40', 'viewport-160x44', 'viewport-240x44'])
def test_advanced_controls_fit_form_content_and_reflow(size, monkeypatch):
    """情境：各視窗尺寸的進階控制應留在表單內且可捲動到達。"""
    _disable_recent_state(monkeypatch)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app._set_advanced_settings_visible(True)
            app.query_one("#manual_pattern_override").value = True
            panel = app.query_one("#form-panel")
            advanced = app.query_one("#advanced-settings")
            controls = [app.query_one(f"#{control_id}") for control_id in ADVANCED_CONTROL_IDS]
            controls.extend(app.query_one(f"#{control_id}") for control_id in ("path", "browse_path", "keyword"))
            await _wait_for_ui_state(
                pilot,
                lambda: (
                    app.query_one("#logback_xml_path").region.width > 0
                ),
                f"advanced controls did not finish reflowing at {size}",
            )
            await _wait_for_layout_to_settle(pilot, (panel, advanced, *controls))

            content = panel.content_region
            assert content.width > 0
            assert content.height >= 3, (size, panel.region, content, app.query_one("#form-actions").region)
            assert advanced.has_class("advanced-wide") is (advanced.content_size.width >= 72)
            assert advanced.has_class("advanced-compact") is (advanced.content_size.width < 72)

            regions = []
            for control in controls:
                region = control.region
                assert content.x <= region.x, (size, control.id, region, content)
                assert region.x + region.width <= content.x + content.width, (size, control.id, region, content)
                regions.append((control.id, region))

            for (first_id, first), (second_id, second) in combinations(regions, 2):
                assert not _overlap(first, second), (size, first_id, first, second_id, second)

            assert panel.max_scroll_y >= 0
            for control_id in (
                "path",
                "browse_path",
                "keyword",
                "logback_xml_path",
                "browse_logback_xml",
                "log_pattern",
                "start_date",
                "start_time",
                "end_date",
                "end_time",
                "ignore_case",
                "sort_by",
                "level_ERROR",
                "level_TRACE",
            ):
                control = app.query_one(f"#{control_id}")
                control.scroll_visible(animate=False)
                await _wait_for_layout_to_settle(
                    pilot, (control, panel),
                    lambda: panel.content_region.contains(
                        control.region.x, control.region.y + control.region.height // 2
                    ),
                )
                region = control.region
                visible_center = region.y + region.height // 2
                assert content.y <= visible_center < content.y + content.height
                for action_id in ("manage-recipes", "run"):
                    assert not _overlap(region, app.query_one(f"#{action_id}").region)

            panel.scroll_y = 0
            await pilot.pause()
            summary = app.query_one("#source-scope-summary")
            summary.scroll_visible(animate=False)
            await _wait_for_layout_to_settle(
                pilot, (summary, panel),
                lambda: _overlap(summary.region, panel.content_region),
            )
            assert _overlap(summary.region, panel.content_region)

            for action_id in ("manage-recipes", "run"):
                action = app.query_one(f"#{action_id}")
                assert 0 <= action.region.y
                assert action.region.y + action.region.height <= size[1] - 1

    asyncio.run(run_check())


def test_advanced_responsive_layout_reflows_after_resize(monkeypatch):
    """情境：執行中調整視窗尺寸後進階設定應重新排版。"""
    _disable_recent_state(monkeypatch)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(240, 44)) as pilot:
            await pilot.pause()
            app._set_advanced_settings_visible(True)
            await pilot.pause()
            advanced = app.query_one("#advanced-settings")
            assert advanced.has_class("advanced-wide")

            await pilot.resize_terminal(80, 32)
            await pilot.pause()
            assert advanced.has_class("advanced-compact"), (
                advanced.region,
                advanced.content_size,
                app.query_one("#content").classes,
                app.query_one("#form-panel").region,
            )
            assert app.query_one("#form-panel").content_region.height > 0

            await pilot.resize_terminal(240, 44)
            await pilot.pause()
            assert advanced.has_class("advanced-wide")
            assert app.query_one("#form-panel").content_region.height > 0

    asyncio.run(run_check())
