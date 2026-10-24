from log_analyzer.presentation.tui.controllers import exporting as exporting_module
import asyncio

import pytest
from textual.widgets import Label

from log_analyzer.application.analysis_service import AnalysisRequest, analyze_logs
from log_analyzer.infrastructure.recipe_store import RecipeStore
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui.recent_state import save_recent_tui_state
from log_analyzer.presentation.tui import LogAnalyzerApp


@pytest.mark.parametrize("size", [(70, 18), (150, 60)], ids=['viewport-70x18', 'viewport-150x60'])
def test_display_mode_control_is_absent_and_other_search_controls_remain(size, tmp_path, monkeypatch):
    """情境：寬窄視窗都不應出現已移除的顯示模式控制且保留其他搜尋控制。"""
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()

            assert len(app.query("#display_mode")) == 0
            assert all(label.render().plain != "顯示模式" for label in app.query(Label))
            app.query_one("#ignore_case")
            app.query_one("#sort_by")

    asyncio.run(run_check())


def test_legacy_recent_display_modes_restore_the_same_form_without_running_analysis(tmp_path, monkeypatch):
    """情境：舊最近設定的顯示模式應還原相同表單而不啟動分析。"""
    monkeypatch.chdir(tmp_path)

    async def restore(mode: str) -> tuple[object, ...]:
        save_recent_tui_state(
            {
                "path": "saved/logs",
                "keyword": "saved keyword",
                "ignore_case": False,
                "display_mode": mode,
                "sort_by": "level",
                "levels": ["ERROR", "TRACE"],
            }
        )
        app = LogAnalyzerApp()
        async with app.run_test(size=(150, 60)) as pilot:
            await pilot.pause()

            assert len(app.query("#display_mode")) == 0
            assert app.query_one("#path").value == "saved/logs"
            assert app.query_one("#keyword").value == "saved keyword"
            assert app.query_one("#ignore_case").value is False
            assert app.query_one("#sort_by").value == "level"
            assert app.query_one("#level_ERROR").value is True
            assert app.query_one("#level_WARN").value is False
            assert app.query_one("#level_TRACE").value is True
            assert app._last_result is None
            assert app._result_state == "idle"

            return (
                app.query_one("#path").value,
                app.query_one("#keyword").value,
                app.query_one("#ignore_case").value,
                app.query_one("#sort_by").value,
                tuple(
                    level
                    for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")
                    if app.query_one(f"#level_{level}").value
                ),
                app._result_state,
            )

    full_snapshot = asyncio.run(restore("full"))
    summary_snapshot = asyncio.run(restore("summary"))
    assert full_snapshot == summary_snapshot


@pytest.mark.parametrize("legacy_mode", ["full", "summary", "unknown"], ids=['legacy-mode-full', 'legacy-mode-summary', 'legacy-mode-unknown'])
def test_legacy_recipe_display_mode_is_ignored_without_replacing_result_or_rewriting_recipe(
    legacy_mode, tmp_path, monkeypatch
):
    """情境：舊配方顯示模式應忽略且不替換結果或重寫配方。"""
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-10-22 12:00:00.000 [main] ERROR com.test.App - retain this result\n",
        encoding="utf-8",
    )
    existing_result = analyze_logs(AnalysisRequest(path=str(logs)))
    store = RecipeStore(tmp_path / "recipes.json")
    store.save(
        "Legacy mode",
        {
            "keyword": "restored keyword",
            "sort_by": "level",
            "display_mode": legacy_mode,
            "levels": ["ERROR"],
        },
    )
    recipe_path = tmp_path / "recipes.json"
    recipe_bytes = recipe_path.read_bytes()
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)
    monkeypatch.setattr(
        tui_module,
        "analyze_logs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("analysis must not run")),
    )
    monkeypatch.setattr(
        exporting_module,
        "export_analysis_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("export must not run")),
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._last_result = existing_result
            app._result_state = "success"

            message = app._apply_recipe_state("Legacy mode", store.get("Legacy mode"))

            assert len(app.query("#display_mode")) == 0
            assert app.query_one("#keyword").value == "restored keyword"
            assert app.query_one("#sort_by").value == "level"
            assert app.query_one("#level_ERROR").value is True
            assert app.query_one("#level_WARN").value is False
            assert "display_mode" not in message
            assert app._last_result is existing_result
            assert app._result_state == "success"
            assert recipe_path.read_bytes() == recipe_bytes

    asyncio.run(run_check())
