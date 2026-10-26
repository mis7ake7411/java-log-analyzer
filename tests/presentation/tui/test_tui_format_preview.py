import asyncio
import os
import threading

import pytest
from textual.widgets import Checkbox, Input, Static

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.screens.format_preview import FormatPreviewScreen
from log_analyzer.domain.logback_xml import LogbackPatternAssessment
from tui_waits import (
    wait_for_layout_to_settle,
    wait_for_ui_state,
    wait_for_button_ready,
    wait_for_scheduled_analysis,
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    """建立來源並隔離最近表單，避免外部設定改變預覽。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda state: None)
    (tmp_path / "app.log").write_text(
        "2026-10-24 10:00:00.000 [main] INFO Service - [ERROR] hello\n",
        encoding="utf-8",
    )
    return tmp_path


def test_format_preview_shows_fields_without_running_analysis_or_changing_form(source):
    """情境：從目前條件開啟預覽，拆出樣本欄位並保留表單及分析狀態。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 42)) as pilot:
            button = app.query_one("#preview-format")
            await wait_for_layout_to_settle(pilot, (button,))
            before = app._capture_recipe_state()
            button.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: bool(app.screen.query("#format-preview-status")),
                "preview not opened",
            )
            screen = app.screen
            await wait_for_ui_state(
                pilot, lambda: not screen.preview_loading, "preview not finished"
            )
            text = screen.query_one("#format-preview-content", Static).render().plain
            assert "2026-10-24 10:00:00.000" in text
            assert "INFO" in text and "Service" in text and "[ERROR] hello" in text
            assert "時間" in text and "Logger" in text and "訊息" in text
            assert app._capture_recipe_state() == before
            assert app._last_result is None
            assert getattr(app, "_scheduled_analysis_task", None) is None
            await pilot.press("escape")
            assert app.screen is not screen

    asyncio.run(check())


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("auto", "XML："),
        ("explicit", "XML："),
        ("manual", "手動覆寫 Pattern"),
        ("blank", "手動 Pattern 不可為空白"),
        ("unsupported", "%springProperty"),
        ("broken-xml", "無法解析 Logback XML"),
        ("recipe-default", "內建 Logback 格式"),
    ],
    ids=[
        "auto-xml",
        "explicit-xml",
        "manual-overrides-xml",
        "blank-manual",
        "unsupported-manual",
        "invalid-xml",
        "legacy-default",
    ],
)
def test_preview_uses_current_format_source_and_reports_errors(source, mode, expected):
    """情境：自動／指定 XML、手動及舊配方使用相同優先序，失敗保留具體原因。"""
    xml = source / "logback.xml"
    pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n"
    xml.write_text(
        f"<configuration><appender name='FILE'><encoder><pattern>{pattern}</pattern></encoder></appender></configuration>",
        encoding="utf-8",
    )

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 40)) as pilot:
            if mode == "explicit":
                app.query_one("#logback_xml_path", Input).value = str(xml)
            elif mode in {"manual", "blank", "unsupported"}:
                app.query_one("#logback_xml_path", Input).value = str(
                    source / "missing.xml"
                )
                app.query_one("#manual_pattern_override", Checkbox).value = True
                app.query_one("#log_pattern", Input).value = (
                    pattern
                    if mode == "manual"
                    else ("" if mode == "blank" else pattern + " %springProperty")
                )
            elif mode == "broken-xml":
                xml.write_text("<configuration>", encoding="utf-8")
            elif mode == "recipe-default":
                app._apply_recipe_state(
                    "legacy", {"path": str(source), "pattern_mode": "default"}
                )
            await wait_for_layout_to_settle(
                pilot, (app.query_one("#condition-overview"),)
            )
            if mode == "blank":
                # 啟用覆寫會先帶入既有格式；模擬使用者再清空輸入。
                app.query_one("#log_pattern", Input).value = ""
                await wait_for_ui_state(
                    pilot,
                    lambda: (
                        "尚未輸入"
                        in app.query_one("#condition-pattern", Static).render().plain
                    ),
                    "manual clear not applied",
                )
            before = app._capture_recipe_state()
            app.action_preview_format()
            screen = app.screen
            assert isinstance(screen, FormatPreviewScreen)
            await wait_for_ui_state(
                pilot, lambda: not screen.preview_loading, "format preview not complete"
            )
            rendered = "\n".join(
                screen.query_one(selector, Static).render().plain
                for selector in ("#format-preview-status", "#format-preview-content")
            )
            assert expected in rendered
            assert app._capture_recipe_state() == before
            assert getattr(app, "_scheduled_analysis_task", None) is None

    asyncio.run(check())


def test_xml_preview_shows_all_tied_candidates_without_applying_one(source):
    """情境：多個 XML 格式平手時均顯示預覽，不在背景選定或啟動分析。"""
    (source / "logback.xml").write_text(
        "<configuration><pattern>%d [%thread] %level %logger - %msg%n</pattern><pattern>%d [%t] %p %c - %m%n</pattern></configuration>",
        encoding="utf-8",
    )

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 40)) as pilot:
            app.action_preview_format()
            screen = app.screen
            await wait_for_ui_state(
                pilot, lambda: not screen.preview_loading, "XML candidates not ready"
            )
            assert screen.preview_result.status == "uncertain"
            assert len(screen.preview_result.candidates) == 2
            text = screen.query_one("#format-preview-content", Static).render().plain
            assert "候選 1" in text and "候選 2" in text
            assert "INFO" in text
            assert not app.query_one("#manual_pattern_override", Checkbox).value
            assert app._last_result is None

    asyncio.run(check())


@pytest.mark.parametrize(
    "size",
    [(160, 45), (120, 40), (80, 40), (80, 24)],
    ids=["wide", "split-panel", "narrow", "short"],
)
def test_format_preview_wraps_long_samples_and_close_stays_visible(source, size):
    """情境：長日誌與路徑在寬窄版換行，關閉按鈕不會被樣本擠出畫面。"""
    (source / "app.log").write_text(
        "2026-10-24 10:00:00.000 [main] WARN Service - "
        + "長訊息 [ERROR] / " * 100
        + "\nnot a log line\n",
        encoding="utf-8",
    )

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            button = app.query_one("#preview-format")
            await wait_for_layout_to_settle(pilot, (button,))
            button.focus()
            assert button.region.height == 1
            await pilot.press("enter")
            screen = app.screen
            await wait_for_ui_state(
                pilot,
                lambda: (
                    isinstance(app.screen, FormatPreviewScreen) and not screen.preview_loading
                ),
                "preview not complete",
            )
            card = screen.query_one("#format-preview-card")
            body = screen.query_one("#format-preview-body")
            close = screen.query_one("#close-format-preview")
            await wait_for_layout_to_settle(pilot, (card, body, close))
            for widget in (card, close):
                assert screen.region.contains_region(widget.region)
            assert close.region.y >= body.region.bottom
            assert body.max_scroll_x == 0
            text = screen.query_one("#format-preview-content", Static).render().plain
            assert "未命中樣本" in text and "不符合 Pattern" in text
            capture = os.environ.get("TUI_CAPTURE_DIR")
            if capture:
                app.save_screenshot(
                    filename=f"format-preview-{size[0]}x{size[1]}.svg", path=capture
                )
            close.focus()
            await pilot.press("enter")
            assert app.screen is not screen

    asyncio.run(check())


def test_preview_preserves_result_and_blocks_main_screen_shortcuts(source):
    """情境：預覽中的主畫面快捷鍵不會重跑分析、清除結果或修改設定。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 42)) as pilot:
            await wait_for_button_ready(app, pilot)
            app.query_one("#run").focus()
            await pilot.press("enter")
            await wait_for_scheduled_analysis(app)
            result = app._last_result
            assert result is not None
            task = app._scheduled_analysis_task
            app.action_preview_format()
            screen = app.screen
            await wait_for_ui_state(
                pilot, lambda: not screen.preview_loading, "preview not ready"
            )
            app.set_focus(None)
            await pilot.press("c", "a", "ctrl+x", "enter")
            assert app.screen is screen
            assert app._last_result is result
            assert app._scheduled_analysis_task is task
            assert not app._advanced_settings_visible

    asyncio.run(check())


def test_closing_preview_during_background_read_is_responsive(source, monkeypatch):
    """情境：抽樣讀取中仍可關閉，晚到的背景結果不會覆寫主畫面。"""
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = FormatPreviewScreen._read_preview

    def gated(screen):
        entered.set()
        assert release.wait(5), "test did not release preview reader"
        try:
            return original(screen)
        finally:
            finished.set()

    monkeypatch.setattr(FormatPreviewScreen, "_read_preview", gated)

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            app.action_preview_format()
            screen = app.screen
            try:
                await wait_for_ui_state(
                    pilot, entered.is_set, "background reader not started"
                )
                await pilot.press("escape")
                assert app.screen is not screen
            finally:
                release.set()
            await wait_for_ui_state(
                pilot, finished.is_set, "background read did not finish"
            )
            assert app._last_result is None
            assert getattr(app, "_scheduled_analysis_task", None) is None

    asyncio.run(check())


def test_long_error_reason_remains_scrollable_with_visible_close_button(
    source, monkeypatch
):
    """情境：多份 XML 的長錯誤說明可捲動，短視窗仍保留關閉操作。"""
    monkeypatch.setattr(
        FormatPreviewScreen,
        "_read_preview",
        lambda screen: LogbackPatternAssessment(
            "unsupported", "格式錯誤與來源說明；" * 100, (), None, 0
        ),
    )

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(80, 24)) as pilot:
            app.action_preview_format()
            screen = app.screen
            await wait_for_ui_state(
                pilot, lambda: not screen.preview_loading, "error preview not ready"
            )
            close = screen.query_one("#close-format-preview")
            await wait_for_layout_to_settle(pilot, (close,))
            assert screen.region.contains_region(close.region)
            assert (
                "格式錯誤與來源說明"
                in screen.query_one("#format-preview-status", Static).render().plain
            )
            assert screen.query_one("#format-preview-body").max_scroll_y > 0

    asyncio.run(check())


def test_preview_respects_selected_file_scope(source):
    """情境：主表單只選一份日誌時，預覽不擴大到旁邊的檔案。"""
    (source / "sibling.log").write_text("sibling must not be read\n", encoding="utf-8")

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 40)) as pilot:
            available = app._resolve_log_source(str(source))
            app._file_selection_state().choose(available, ("app.log",))
            app.action_preview_format()
            screen = app.screen
            await wait_for_ui_state(
                pilot, lambda: not screen.preview_loading, "selected preview not ready"
            )
            candidate = screen.preview_result.candidates[0]
            assert candidate.checked == candidate.matches == 1
            assert screen.source.filenames == ("app.log",)
            assert (
                "sibling must not be read"
                not in screen.query_one("#format-preview-content", Static)
                .render()
                .plain
            )

    asyncio.run(check())
