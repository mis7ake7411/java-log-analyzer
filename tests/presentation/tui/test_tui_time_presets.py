"""常用時間範圍選單的端到端驗收。"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import Input, Select, Static

from log_analyzer.infrastructure.recipe_store import RecipeStore
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.components import time_range as time_range_module
from tui_waits import (
    wait_for_button_ready,
    wait_for_scheduled_analysis,
    wait_for_ui_state,
)


_PRESET_INDEX = {"none": 0, "today": 1, "last_hour": 2, "custom": 3}


@pytest.fixture(autouse=True)
def _isolate_recent_tui_state(tmp_path, monkeypatch):
    """隔離每個測試的工作目錄與近期表單狀態，避免讀寫使用者資料。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)


async def _open_advanced_settings(app: LogAnalyzerApp, pilot) -> None:
    app._set_advanced_settings_visible(True)
    await wait_for_ui_state(
        pilot,
        lambda: app.query_one("#advanced-settings").display,
        "advanced settings did not become visible",
    )


async def _select_preset(pilot, app: LogAnalyzerApp, preset: str) -> None:
    """透過選單鍵盤操作選取常用時間範圍。"""
    select = app.query_one("#time-preset", Select)
    panel = app.query_one("#form-panel")
    time_range = app.query_one(time_range_module.TimeRangeSettings)
    custom_fields = app.query_one("#time-range-custom")
    previous_preset = select.value
    previous_snapshot = time_range._pending_input_snapshot
    previous_values = time_range._read_values()
    select.scroll_visible(animate=False)
    await wait_for_ui_state(
        pilot,
        lambda: panel.content_region.contains(
            select.region.x, select.region.y + select.region.height // 2
        ),
        "time preset did not scroll into the form viewport",
    )
    await pilot.click(select)
    overlay = select.query_one("SelectOverlay")
    await wait_for_ui_state(
        pilot,
        lambda: (
            select.expanded
            and select.has_class("-expanded")
            and overlay.region.width > 0
            and overlay.region.height > 0
        ),
        "time preset options overlay did not become ready",
    )
    current_index = _PRESET_INDEX[select.value]
    difference = _PRESET_INDEX[preset] - current_index
    direction = "down" if difference > 0 else "up"
    for _ in range(abs(difference)):
        await pilot.press(direction)
    await pilot.press("enter")
    if preset == "custom":
        ready = lambda: (
            select.value == preset
            and not select.expanded
            and custom_fields.display
            and time_range._pending_input_snapshot is None
            and time_range._read_values() == previous_values
        )
    else:
        ready = lambda: (
            select.value == preset
            and not select.expanded
            and not custom_fields.display
            and time_range._pending_input_snapshot is not None
            and time_range._read_values() == time_range._pending_input_snapshot
            and (
                previous_preset == preset
                or time_range._pending_input_snapshot != previous_snapshot
            )
        )
    await wait_for_ui_state(
        pilot,
        ready,
        f"time preset {preset!r} did not finish applying its input values",
    )


def _saved_time_recipe() -> dict[str, object]:
    return {
        "path": ".",
        "pattern_mode": "auto",
        "log_pattern": "",
        "logback_xml_path": "",
        "start_date": "2026-10-24",
        "start_time": "10:00:00",
        "end_date": "2026-10-26",
        "end_time": "23:59:59",
        "keyword": "",
        "ignore_case": False,
        "level_unrestricted": True,
        "levels": ["ERROR", "WARN", "INFO", "DEBUG", "TRACE"],
        "sort_by": "time",
        "display_mode": "full",
    }


def _prepare_saved_recipe_workspace(tmp_path, monkeypatch) -> RecipeStore:
    """建立可分析來源與已儲存時間配方的隔離工作區。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    (tmp_path / "app.log").write_text(
        "2026-10-24 10:00:00.000 [main] INFO Service - original\n",
        encoding="utf-8",
    )
    store = RecipeStore(tmp_path / "recipes.json")
    store.save("Saved time range", _saved_time_recipe())
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)
    return store


def _track_schedule_calls(app: LogAnalyzerApp, monkeypatch) -> list[bool]:
    """記錄分析排程入口呼叫次數，同時維持原本分析行為。"""
    schedule_calls = []
    original_schedule = app._schedule_analysis_start

    def record_schedule() -> None:
        schedule_calls.append(True)
        original_schedule()

    monkeypatch.setattr(app, "_schedule_analysis_start", record_schedule)
    return schedule_calls


def _track_analysis_requests(app: LogAnalyzerApp, monkeypatch) -> list[object]:
    """記錄實際送入分析的請求，同時委派給原本分析流程。"""
    analysis_requests = []
    original_analyze = app._analyze_request

    async def record_analysis(request):
        analysis_requests.append(request)
        return await original_analyze(request)

    monkeypatch.setattr(app, "_analyze_request", record_analysis)
    return analysis_requests


def _read_request_range(
    app: LogAnalyzerApp,
) -> tuple[datetime | None, datetime | None]:
    """讀取表單解析後的本機時間區間，並去除時區供固定時間斷言。"""
    request, _ = app._collect_form_values()
    start = request.start_dt.replace(tzinfo=None) if request.start_dt else None
    end = request.end_dt.replace(tzinfo=None) if request.end_dt else None
    return start, end


async def _apply_saved_recipe(app: LogAnalyzerApp, pilot) -> None:
    """透過配方管理畫面載入既有時間範圍配方。"""
    await wait_for_button_ready(app, pilot, "#manage-recipes")
    await pilot.click("#manage-recipes")
    await wait_for_ui_state(
        pilot,
        lambda: len(app.screen.query("#recipe-selection")) == 1,
        "recipe manager did not open",
    )
    recipe_screen = app.screen
    recipe_screen.query_one("#recipe-selection", Select).value = "Saved time range"
    await wait_for_button_ready(app, pilot, "#recipe-apply")
    await pilot.click("#recipe-apply")
    await wait_for_ui_state(
        pilot,
        lambda: app.screen is not recipe_screen,
        "saved recipe was not applied",
    )


def test_time_range_settings_expose_a_preset_select():
    """情境：時間範圍應以選單呈現，而不是直接顯示四個欄位。"""

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert len(app.query("#time-preset")) == 1

    asyncio.run(run_check())


def test_today_preset_uses_local_date_and_does_not_schedule_analysis(monkeypatch):
    """情境：選今天後填入當日範圍、保留含邊界解析且不自動分析。"""
    local_now = datetime(
        2026, 10, 26, 0, 15, 42, 999123, tzinfo=timezone(timedelta(hours=8))
    )
    monkeypatch.setattr(time_range_module, "_local_now", lambda: local_now)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        scheduled = []
        original_schedule = app._schedule_analysis_start

        def record_schedule() -> None:
            scheduled.append(True)
            original_schedule()

        monkeypatch.setattr(app, "_schedule_analysis_start", record_schedule)
        async with app.run_test(size=(160, 44)) as pilot:
            await _open_advanced_settings(app, pilot)
            await _select_preset(pilot, app, "today")

            assert app.query_one("#time-preset", Select).value == "today"
            assert app.query_one("#time-range-custom").display is False
            assert app.query_one("#start_date", Input).value == "2026-10-26"
            assert app.query_one("#start_time", Input).value == ""
            assert app.query_one("#end_date", Input).value == "2026-10-26"
            assert app.query_one("#end_time", Input).value == ""
            assert "2026-10-26 00:00:00" in app.query_one(
                "#time-range-summary", Static
            ).render().plain
            assert "2026-10-26 23:59:59" in app.query_one(
                "#time-range-summary", Static
            ).render().plain

            request, _ = app._collect_form_values()
            assert request.start_dt.replace(tzinfo=None) == datetime(2026, 10, 26)
            assert request.end_dt.replace(tzinfo=None) == datetime(
                2026, 10, 26, 23, 59, 59
            )
            assert scheduled == []

    asyncio.run(run_check())


def test_last_hour_crosses_midnight_and_syncs_app_state_without_analysis(
    tmp_path, monkeypatch
):
    """情境：最近一小時跨日後同步條件、配方差異與結果提示但不重跑。"""
    local_now = datetime(
        2026, 10, 26, 0, 15, 42, 999123, tzinfo=timezone(timedelta(hours=8))
    )
    monkeypatch.setattr(time_range_module, "_local_now", lambda: local_now)
    _prepare_saved_recipe_workspace(tmp_path, monkeypatch)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        schedule_calls = _track_schedule_calls(app, monkeypatch)
        analysis_requests = _track_analysis_requests(app, monkeypatch)

        async with app.run_test(size=(160, 44)) as pilot:
            await _apply_saved_recipe(app, pilot)

            assert app.query_one("#time-preset", Select).value == "custom"
            assert app.query_one("#time-range-custom").display is True
            assert app.query_one("#start_date", Input).value == "2026-10-24"
            assert app.query_one("#start_time", Input).value == "10:00:00"
            assert app.query_one("#end_date", Input).value == "2026-10-26"
            assert app.query_one("#end_time", Input).value == "23:59:59"
            assert "Saved time range" in app.query_one(
                "#recipe-feedback", Static
            ).render().plain

            await wait_for_button_ready(app, pilot, "#run")
            await pilot.click("#run")
            await wait_for_scheduled_analysis(app)
            assert len(analysis_requests) == 1
            original_result = app._last_result
            assert original_result is not None
            assert schedule_calls == [True]

            await _open_advanced_settings(app, pilot)
            await _select_preset(pilot, app, "last_hour")
            await wait_for_ui_state(
                pilot,
                lambda: "2026-10-25 23:15:42"
                in app.query_one("#condition-time", Static).render().plain,
                "preset changes did not reach the condition overview",
            )
            await wait_for_ui_state(
                pilot,
                lambda: "已修改"
                in app.query_one("#recipe-feedback", Static).render().plain,
                "preset changes did not mark the loaded recipe modified",
            )
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#result-context-notice", Static).display,
                "preset changes did not mark the preserved result as stale",
            )

            assert app.query_one("#start_date", Input).value == "2026-10-25"
            assert app.query_one("#start_time", Input).value == "23:15:42"
            assert app.query_one("#end_date", Input).value == "2026-10-26"
            assert app.query_one("#end_time", Input).value == "00:15:42"
            assert _read_request_range(app) == (
                datetime(2026, 10, 25, 23, 15, 42),
                datetime(2026, 10, 26, 0, 15, 42),
            )
            assert app._last_result is original_result
            assert len(analysis_requests) == 1
            assert schedule_calls == [True]

            app.query_one("#clear-condition-time").focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: (
                    app.query_one("#time-preset", Select).value == "none"
                    and not app.query_one("#time-range-custom").display
                    and "不限" in app.query_one("#condition-time", Static).render().plain
                ),
                "clearing the time condition did not reset the preset",
            )
            assert [
                app.query_one(f"#{field_id}", Input).value
                for field_id in ("start_date", "start_time", "end_date", "end_time")
            ] == ["", "", "", ""]
            assert _read_request_range(app) == (None, None)
            assert "已修改" in app.query_one(
                "#recipe-feedback", Static
            ).render().plain
            assert app.query_one("#result-context-notice", Static).display
            assert app._last_result is original_result
            assert len(analysis_requests) == 1
            assert schedule_calls == [True]

    asyncio.run(run_check())


def test_recent_time_values_restore_as_custom_without_being_overwritten(monkeypatch):
    """情境：近期狀態載入四欄後選單轉自訂且初始選單事件不清除原值。"""
    restored_values = {
        "start_date": "2026-10-24",
        "start_time": "08:30:15",
        "end_date": "2026-10-24",
        "end_time": "09:45:30",
    }
    monkeypatch.setattr(
        tui_module,
        "load_recent_tui_state",
        lambda: dict(restored_values),
    )
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        scheduled = []
        monkeypatch.setattr(
            app, "_schedule_analysis_start", lambda: scheduled.append(True)
        )
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause()
            assert app.query_one("#time-preset", Select).value == "custom"
            assert app.query_one("#time-range-custom").display is True
            assert {
                field_id: app.query_one(f"#{field_id}", Input).value
                for field_id in restored_values
            } == restored_values
            assert "2026-10-24 08:30:15" in app.query_one(
                "#time-range-summary", Static
            ).render().plain
            request, _ = app._collect_form_values()
            assert request.start_dt.replace(tzinfo=None) == datetime(
                2026, 10, 24, 8, 30, 15
            )
            assert request.end_dt.replace(tzinfo=None) == datetime(
                2026, 10, 24, 9, 45, 30
            )
            assert scheduled == []

    asyncio.run(run_check())


def test_custom_input_keeps_request_values_and_marks_invalid_summary(monkeypatch):
    """情境：自訂欄位保留明確請求值，無效輸入顯示待完成而不偽稱實際範圍。"""

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await _open_advanced_settings(app, pilot)
            await _select_preset(pilot, app, "custom")
            assert app.query_one("#time-range-custom").display is True
            start_date = app.query_one("#start_date", Input)
            panel = app.query_one("#form-panel")
            start_date.scroll_visible(animate=False)
            await wait_for_ui_state(
                pilot,
                lambda: panel.content_region.contains(
                    start_date.region.x,
                    start_date.region.y + start_date.region.height // 2,
                ),
                "custom start date did not scroll into the form viewport",
            )
            await pilot.click(start_date)
            await pilot.press(*"2026-10-26")
            await pilot.pause()
            assert start_date.value == "2026-10-26"
            app.query_one("#start_time", Input).value = "08:30:15"
            app.query_one("#end_date", Input).value = "2026-10-26"
            app.query_one("#end_time", Input).value = "09:45:30"
            await pilot.pause()

            request, _ = app._collect_form_values()
            assert app.query_one("#time-preset", Select).value == "custom"
            assert request.start_dt.replace(tzinfo=None) == datetime(
                2026, 10, 26, 8, 30, 15
            )
            assert request.end_dt.replace(tzinfo=None) == datetime(
                2026, 10, 26, 9, 45, 30
            )
            assert "實際範圍：2026-10-26 08:30:15 ～ 2026-10-26 09:45:30" in (
                app.query_one("#time-range-summary", Static).render().plain
            )

            app.query_one("#start_date", Input).value = "not-a-date"
            await pilot.pause()
            summary = app.query_one("#time-range-summary", Static).render().plain
            assert "自訂範圍尚未完成" in summary
            assert "not-a-date" in summary
            assert "實際範圍" not in summary
            try:
                app._collect_form_values()
            except ValueError:
                pass
            else:
                raise AssertionError("invalid custom range should remain a ValueError")

    asyncio.run(run_check())
