import asyncio

from textual.widgets import Collapsible, Input, Static
from tui_waits import wait_for_layout_to_settle, wait_for_ui_state

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp


def _disable_recent_state(monkeypatch):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)


def test_manual_override_shows_only_input_and_one_hint(monkeypatch):
    """情境：手動覆寫應只顯示輸入與一則必要提示。"""
    _disable_recent_state(monkeypatch)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_advanced_settings_visible(True)
            checkbox = app.query_one("#manual_pattern_override")
            checkbox.value = True
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#pattern-field").display,
                "manual override visibility was not applied",
            )

            assert app.query_one("#log_pattern", Input).display
            assert not app.query_one("#pattern-source", Static).display
            assert not app.query_one("#pattern-details", Collapsible).display
            hint = app.query_one("#pattern-field",).query_one(".field-hint", Static)
            assert hint.display
            assert hint.render().plain.strip()

    asyncio.run(run_check())


def test_automatic_pattern_preview_is_collapsed_and_can_be_expanded(monkeypatch):
    """情境：自動 Pattern 預覽應預設收合且能展開。"""
    _disable_recent_state(monkeypatch)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._set_advanced_settings_visible(True)

            source = app.query_one("#pattern-source", Static)
            details = app.query_one("#pattern-details", Collapsible)
            preview = app.query_one("#pattern-preview", Static)
            assert source.display
            assert details.display
            assert details.collapsed

            details.collapsed = False
            await wait_for_layout_to_settle(pilot, (details, preview))
            assert not details.collapsed
            assert preview.region.height > 0

    asyncio.run(run_check())


def test_long_pattern_wraps_without_overlapping_actions_at_common_sizes(monkeypatch):
    """情境：長 Pattern 在常用寬窄尺寸應換行且不遮蓋操作按鈕。"""
    _disable_recent_state(monkeypatch)
    long_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n " * 8

    async def check_size(size: tuple[int, int]) -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app._set_advanced_settings_visible(True)
            app._set_pattern_preview("測試來源", long_pattern)
            details = app.query_one("#pattern-details", Collapsible)
            details.collapsed = False
            await pilot.pause()

            preview = app.query_one("#pattern-preview", Static)
            run_button = app.query_one("#run")
            panel = app.query_one("#form-panel")
            await wait_for_layout_to_settle(pilot, (preview, panel, run_button))
            preview.scroll_visible(animate=False)
            await wait_for_layout_to_settle(
                pilot,
                (preview, panel, run_button),
                lambda: preview.region.overlaps(panel.content_region),
            )
            assert preview.region.width <= panel.content_region.width
            assert preview.region.height > 1
            visible_top = max(preview.region.y, panel.content_region.y)
            visible_bottom = min(
                preview.region.y + preview.region.height,
                panel.content_region.y + panel.content_region.height,
            )
            assert visible_top < visible_bottom
            assert not (
                preview.region.x < run_button.region.x + run_button.region.width
                and run_button.region.x < preview.region.x + preview.region.width
                and visible_top < run_button.region.y + run_button.region.height
                and run_button.region.y < visible_bottom
            )

    asyncio.run(check_size((80, 32)))
    asyncio.run(check_size((160, 44)))
