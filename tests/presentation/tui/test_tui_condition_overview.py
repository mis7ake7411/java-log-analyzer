import asyncio
import os

import pytest
from textual.widgets import Checkbox, Input, Static

from tui_waits import (
    wait_for_ui_state,
    wait_for_button_ready,
    wait_for_scheduled_analysis,
    wait_for_layout_to_settle,
)
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui.app import LogAnalyzerApp


@pytest.fixture
def source(tmp_path, monkeypatch):
    """隔離最近表單狀態，建立含兩份日誌的來源。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    for name in ("a.log", "b.log"):
        (tmp_path / name).write_text(
            "2026-10-24 10:00:00.000 [main] INFO Service - hello\n", encoding="utf-8"
        )
    return tmp_path


def test_collapsed_overview_tracks_fields_and_individual_reset(source):
    """情境：收合進階設定仍看得到條件，個別清除會修改實際請求且不啟動分析。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            overview = app.query_one("#condition-overview")
            assert overview.display
            assert not app.query_one("#advanced-settings").display
            await wait_for_ui_state(
                pilot,
                lambda: "2" in app.query_one("#condition-files", Static).render().plain,
                "initial source count not rendered",
            )
            app.query_one("#keyword", Input).value = "[ERROR] hello"
            app.query_one("#start_date", Input).value = "2026-10-24"
            app.query_one("#start_time", Input).value = "10:00"
            await wait_for_ui_state(
                pilot,
                lambda: (
                    "[ERROR] hello"
                    in app.query_one("#condition-keyword", Static).render().plain
                ),
                "overview did not reflect keyword",
            )
            for key in ("keyword", "time", "levels"):
                button = app.query_one(f"#clear-condition-{key}")
                button.focus()
                await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#level_unrestricted", Checkbox).value,
                "level reset was not applied",
            )
            request, _ = app._collect_form_values()
            assert request.keyword is None
            assert request.start_dt is None and request.end_dt is None
            assert request.levels is None
            assert app._last_result is None
            assert not getattr(app, "_operation_in_progress", False)

    asyncio.run(check())


@pytest.mark.parametrize(
    "mode",
    ["custom", "auto", "default"],
    ids=["manual-pattern", "explicit-xml", "legacy-default-recipe"],
)
def test_pattern_reset_restores_auto_detection_and_recipe_summary(source, mode):
    """情境：手動、指定 XML 或舊版配方均可恢復自動，不會啟動分析。"""
    xml = source / "logback.xml"
    xml.write_text(
        "<configuration><appender name='FILE'><encoder><pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n</pattern></encoder></appender></configuration>",
        encoding="utf-8",
    )

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app._apply_recipe_state(
                "test",
                {
                    "path": str(source),
                    "keyword": "recipe query",
                    "pattern_mode": mode,
                    "log_pattern": "%msg%n",
                    "logback_xml_path": str(xml),
                },
            )
            expected = {
                "custom": "手動覆寫",
                "auto": "指定 XML",
                "default": "內建 Logback（配方）",
            }[mode]
            await wait_for_ui_state(
                pilot,
                lambda: (
                    expected
                    in app.query_one("#condition-pattern", Static).render().plain
                ),
                "recipe pattern summary not updated",
            )
            assert (
                "recipe query"
                in app.query_one("#condition-keyword", Static).render().plain
            )
            app.query_one("#clear-condition-pattern").focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: (
                    "自動 XML"
                    in app.query_one("#condition-pattern", Static).render().plain
                ),
                "pattern was not restored to auto XML",
            )
            assert not app.query_one("#manual_pattern_override", Checkbox).value
            assert app.query_one("#logback_xml_path", Input).value == str(xml)
            assert app._last_result is None
            assert not getattr(app, "_operation_in_progress", False)

    asyncio.run(check())


def test_reset_keeps_previous_analysis_and_marks_changed_conditions(source):
    """情境：清除條件保留上次分析及其請求，並提示需要重新分析。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            app.query_one("#keyword", Input).value = "hello"
            await wait_for_button_ready(app, pilot)
            app.query_one("#run").focus()
            await pilot.press("enter")
            await wait_for_scheduled_analysis(app)
            result = app._last_result
            assert result is not None
            app.query_one("#clear-condition-keyword").focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#result-context-notice").display,
                "changed conditions were not marked",
            )
            assert app._last_result is result
            assert result.analysis_request.keyword == "hello"
            assert app._collect_form_values()[0].keyword is None

    asyncio.run(check())


def test_file_reset_and_invalid_source_do_not_show_stale_count(source):
    """情境：檔案全選保留來源路徑，無效來源不顯示舊檔案數。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 40)) as pilot:
            available = app._resolve_log_source(str(source))
            app._file_selection_state().choose(available, ("a.log",))
            app.update_path_preview(str(source))
            await wait_for_ui_state(
                pilot,
                lambda: (
                    "1 / 2" in app.query_one("#condition-files", Static).render().plain
                ),
                "selected count not updated",
            )
            app.query_one("#clear-condition-files").focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: app._file_selection_state().selected_files is None,
                "files were not reset",
            )
            assert len(app._resolve_log_source(str(source)).filenames) == 2
            app.query_one("#path", Input).value = str(source / "missing")
            await wait_for_ui_state(
                pilot,
                lambda: (
                    "無法讀取"
                    in app.query_one("#condition-files", Static).render().plain
                ),
                "stale count remained",
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "size",
    [(160, 45), (120, 40), (80, 40), (80, 24)],
    ids=["wide", "split-panel", "narrow", "short"],
)
def test_overview_wraps_and_remains_keyboard_accessible(source, size):
    """情境：長條件在寬窄版均換行，短視窗可用鍵盤捲動到重設操作。"""

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            keyword = "[ERROR] 長條件 / " * 8
            app.query_one("#keyword", Input).value = keyword
            await wait_for_ui_state(
                pilot,
                lambda: (
                    keyword.strip()
                    in app.query_one("#condition-keyword", Static).render().plain
                ),
                "keyword not ready",
            )
            await wait_for_layout_to_settle(
                pilot, (app.query_one("#condition-overview"),)
            )
            overview = app.query_one("#condition-overview")
            for row in overview.query(".condition-row"):
                assert row.region.width > 0
                assert row.region.x >= overview.content_region.x
                assert row.region.right <= overview.content_region.right
                for child in row.children:
                    assert child.region.x >= row.region.x
                    assert child.region.right <= row.region.right
            button = app.query_one("#clear-condition-keyword")
            button.focus()
            await wait_for_layout_to_settle(
                pilot, (button, app.query_one("#form-panel"))
            )
            assert button.has_focus
            assert button.region.height == 1
            assert (
                button.region.intersection(
                    app.query_one("#form-panel").content_region
                ).height
                > 0
            )
            capture = os.environ.get("TUI_CAPTURE_DIR")
            if capture:
                app.save_screenshot(
                    filename=f"conditions-{size[0]}x{size[1]}.svg", path=capture
                )
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: not app.query_one("#keyword", Input).value,
                "keyboard reset did not work",
            )

    asyncio.run(check())
