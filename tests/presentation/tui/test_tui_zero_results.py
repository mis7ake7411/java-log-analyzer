import asyncio
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import views as tui_views
from log_analyzer.presentation.tui.recent_state import load_recent_tui_state, save_recent_tui_state
from log_analyzer.presentation.tui import LogAnalyzerApp


def _render_zero_result(diagnostics: ScanDiagnostics) -> str:
    builder = getattr(tui_views, "build_zero_result_view", None)
    assert callable(builder), "zero-result view builder should be available"
    rendered = StringIO()
    Console(file=rendered, width=120).print(builder(diagnostics))
    return rendered.getvalue()


_RETRY_FIELDS = (
    "start_date",
    "start_time",
    "end_date",
    "end_time",
    "keyword",
    "level_unrestricted",
    "level_ERROR",
    "level_WARN",
    "level_INFO",
    "level_DEBUG",
    "level_TRACE",
)
_RETRY_BUTTONS = {
    "time": "#retry_without_time",
    "level": "#retry_without_level",
    "keyword": "#retry_without_keyword",
}


def _read_retry_form(app: LogAnalyzerApp) -> dict[str, object]:
    return {field_id: app.query_one(f"#{field_id}").value for field_id in _RETRY_FIELDS}


def _prepare_retry_form(app: LogAnalyzerApp, logs: Path, output_dir: Path, retry_filter: str) -> dict[str, object]:
    app.query_one("#path").value = str(logs)
    app.query_one("#output_path").value = str(output_dir)
    for field_id in ("start_date", "start_time", "end_date", "end_time"):
        app.query_one(f"#{field_id}").value = ""

    if retry_filter == "time":
        app.query_one("#start_date").value = "2026-06-08"
        app.query_one("#start_time").value = "00:00"
        app.query_one("#keyword").value = "Started"
    elif retry_filter == "level":
        app.query_one("#level_ERROR").value = True
        app.query_one("#level_WARN").value = False
        app.query_one("#level_INFO").value = False
        app.query_one("#keyword").value = "Started"
    else:
        app.query_one("#keyword").value = "not-present"

    return _read_retry_form(app)


def _assert_retry_buttons_reflect_active_filters(app: LogAnalyzerApp, before: dict[str, object]) -> None:
    active_filters = {
        "time": any(before[field_id] for field_id in _RETRY_FIELDS[:4]),
        "level": not before["level_unrestricted"],
        "keyword": bool(before["keyword"]),
    }
    for filter_name, selector in _RETRY_BUTTONS.items():
        assert app.query_one(selector).disabled is (not active_filters[filter_name])


def _expected_retry_form(before: dict[str, object], retry_filter: str) -> dict[str, object]:
    expected = dict(before)
    if retry_filter == "time":
        for field_id in _RETRY_FIELDS[:4]:
            expected[field_id] = ""
    elif retry_filter == "level":
        expected["level_unrestricted"] = True
    else:
        expected["keyword"] = ""
    return expected


def _assert_retry_succeeded(app: LogAnalyzerApp, retry_filter: str) -> None:
    assert "完成" in str(app.query_one("#result-status").render())
    result = app._last_result
    assert result is not None
    assert result.diagnostics.after_keyword == 1
    if retry_filter == "level":
        assert result.diagnostics.after_level == result.diagnostics.parsed_entries
        assert app.query_one("#level_unrestricted").value is True
        assert all(
            app.query_one(f"#level_{level}").disabled
            for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")
        )
        assert app.query_one("#select_all_levels").disabled is True
        assert app.query_one("#clear_levels").disabled is True
    assert app.query_one("#browse-events").disabled is False
    assert app.query_one("#zero-result-actions").has_class("zero-results-hidden")


async def _exercise_zero_result_retry(tmp_path, monkeypatch, retry_filter: str) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-06-07 12:00:00.000 [main] INFO  com.test.App - Started\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    app = LogAnalyzerApp()
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        assert len(list(app.query("#level_unrestricted"))) == 1
        before = _prepare_retry_form(app, logs, tmp_path, retry_filter)
        await app.action_run_analysis()
        await pilot.pause()
        assert "沒有符合資料" in str(app.query_one("#result-status").render())
        assert app._last_result is None
        _assert_retry_buttons_reflect_active_filters(app, before)

        button = app.query_one(_RETRY_BUTTONS[retry_filter])
        await pilot.click(button, offset=(button.region.width // 2, button.region.height // 2))
        await pilot.pause()

        assert _read_retry_form(app) == _expected_retry_form(before, retry_filter)
        _assert_retry_succeeded(app, retry_filter)


def test_zero_result_view_shows_all_five_stage_counts():
    rendered = _render_zero_result(ScanDiagnostics(6, 5, 4, 3, 0))

    assert "掃描 .log 檔案" in rendered
    assert "符合 Pattern 的事件" in rendered
    assert "時間範圍後" in rendered
    assert "Level 後" in rendered
    assert "關鍵字後" in rendered
    for count in (6, 5, 4, 3, 0):
        assert str(count) in rendered


@pytest.mark.parametrize(
    ("diagnostics", "expected_reason"),
    [
        (ScanDiagnostics(), "找不到 .log 檔案"),
        (ScanDiagnostics(2, 0, 0, 0, 0), "目前的 Logback Pattern"),
        (ScanDiagnostics(2, 2, 0, 0, 0), "時間解析或時間範圍"),
        (ScanDiagnostics(2, 2, 2, 0, 0), "Level 篩選排除了所有"),
        (ScanDiagnostics(2, 2, 2, 2, 0), "關鍵字篩選排除了所有"),
    ],
)
def test_zero_result_view_explains_first_empty_stage(diagnostics, expected_reason):
    rendered = _render_zero_result(diagnostics)

    assert expected_reason in rendered


def test_recent_form_state_persists_level_unrestricted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    save_recent_tui_state({"levels": ["WARN"], "level_unrestricted": True})

    assert load_recent_tui_state() == {"levels": ["WARN"], "level_unrestricted": True}


def test_form_restores_level_unrestricted_and_selected_levels(monkeypatch):
    monkeypatch.setattr(
        tui_module,
        "load_recent_tui_state",
        lambda: {"levels": ["WARN"], "level_unrestricted": True},
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 60)) as pilot:
            await pilot.pause()
            assert len(list(app.query("#level_unrestricted"))) == 1
            assert app.query_one("#level_unrestricted").value is True
            assert app.query_one("#level_WARN").value is True
            assert app.query_one("#level_ERROR").value is False
            assert app.query_one("#level_WARN").disabled is True
            assert app.query_one("#select_all_levels").disabled is True
            assert app.query_one("#clear_levels").disabled is True

    asyncio.run(run_check())


def test_unrestricted_level_disables_controls_without_losing_selection(monkeypatch):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 80)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            await pilot.pause()
            levels = {"ERROR": True, "WARN": False, "INFO": True, "DEBUG": False, "TRACE": True}
            for level, selected in levels.items():
                app.query_one(f"#level_{level}").value = selected

            unrestricted = app.query_one("#level_unrestricted")
            unrestricted.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            form_viewport = app.query_one("#form-panel").scrollable_content_region
            assert unrestricted.region.y >= form_viewport.y
            assert unrestricted.region.y + unrestricted.region.height <= (
                form_viewport.y + form_viewport.height
            )
            await pilot.click("#level_unrestricted")
            await pilot.pause()
            assert unrestricted.value is True

            for level, selected in levels.items():
                checkbox = app.query_one(f"#level_{level}")
                assert checkbox.value is selected
                assert checkbox.disabled is True
            assert app.query_one("#select_all_levels").disabled is True
            assert app.query_one("#clear_levels").disabled is True

            app._set_all_levels(False)
            assert {level: app.query_one(f"#level_{level}").value for level in levels} == levels

            await pilot.click("#level_unrestricted")
            await pilot.pause()
            assert unrestricted.value is False
            for level, selected in levels.items():
                checkbox = app.query_one(f"#level_{level}")
                assert checkbox.value is selected
                assert checkbox.disabled is False
            assert app.query_one("#select_all_levels").disabled is False
            assert app.query_one("#clear_levels").disabled is False

    asyncio.run(run_check())


@pytest.mark.parametrize("retry_filter", ["time", "level", "keyword"])
def test_zero_result_retry_changes_only_its_filter_and_runs_real_scan(tmp_path, monkeypatch, retry_filter):
    asyncio.run(_exercise_zero_result_retry(tmp_path, monkeypatch, retry_filter))


def test_zero_result_can_be_cleared_without_analysis_result(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 60)) as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(logs)
            app.query_one("#output_path").value = str(tmp_path)
            await app.action_run_analysis()

            assert app._last_result is None
            assert app.query_one("#clear").disabled is False
            assert not app.query_one("#zero-result-actions").has_class("zero-results-hidden")

            await pilot.click("#clear")
            await pilot.pause()

            assert "尚未分析" in str(app.query_one("#result-status").render())
            assert app._result_state == "idle"
            assert app.query_one("#clear").disabled is True
            assert app.query_one("#browse-events").disabled is True
            assert app.query_one("#zero-result-actions").has_class("zero-results-hidden")

    asyncio.run(run_check())
