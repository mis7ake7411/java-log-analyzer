import asyncio
from types import SimpleNamespace

from log_analyzer.application.analysis_service import AnalysisRequest, analyze_logs
from log_analyzer.infrastructure.recipe_store import RECIPE_FIELDS, RecipeStore
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp


def _assert_recipe_form_matches(app, saved):
    for field in (
        "path",
        "log_pattern",
        "logback_xml_path",
        "start_date",
        "start_time",
        "end_date",
        "end_time",
        "keyword",
        "sort_by",
    ):
        assert app.query_one(f"#{field}").value == saved[field]
    assert app.query_one("#manual_pattern_override").value is (saved.get("pattern_mode") == "custom")
    for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
        assert app.query_one(f"#level_{level}").value == (level in saved["levels"])
    assert app.query_one("#ignore_case").value is saved["ignore_case"]
    assert app.query_one("#level_unrestricted").value is saved["level_unrestricted"]


def test_recipe_management_control_is_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert len(app.query("#manage-recipes")) == 1
            assert app.query_one("#recipe-feedback").display is False

    asyncio.run(run_check())


def test_recipe_button_opens_a_modal_backed_by_a_temporary_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store_path = tmp_path / "recipes.json"
    monkeypatch.setattr(
        tui_module,
        "RecipeStore",
        lambda: RecipeStore(store_path),
        raising=False,
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.click("#manage-recipes")
            await pilot.pause()

            assert len(app.screen.query("#recipe-dialog")) == 1
            assert not store_path.exists()

    asyncio.run(run_check())


def test_recipe_modal_does_not_open_during_an_operation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            initial_screen = app.screen
            app._operation_in_progress = True
            await pilot.click("#manage-recipes")
            await pilot.pause()

            assert app.screen is initial_screen
            assert len(app.query("#recipe-dialog")) == 0

    asyncio.run(run_check())


def test_save_captures_only_recipe_fields_without_validating_or_running_analysis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)
    monkeypatch.setattr(
        tui_module,
        "analyze_logs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("analysis must not run")),
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app.query_one("#path").value = "incomplete-source"
            app.query_one("#start_date").value = "intentionally incomplete"
            app.query_one("#keyword").value = "[red]literal[/red]"
            app.query_one("#manual_pattern_override").value = True
            app.query_one("#log_pattern").value = "%msg%n"
            app.query_one("#logback_xml_path").value = "C:/logs/logback.xml"
            app.query_one("#start_time").value = "08:30"
            app.query_one("#end_date").value = "2026-10-22"
            app.query_one("#end_time").value = "09:30"
            app.query_one("#ignore_case").value = True
            app.query_one("#level_unrestricted").value = False
            app.query_one("#level_ERROR").value = True
            app.query_one("#level_WARN").value = False
            app.query_one("#level_INFO").value = True
            app.query_one("#level_DEBUG").value = False
            app.query_one("#level_TRACE").value = True
            app.query_one("#sort_by").value = "level"
            app.query_one("#output_path").value = "do-not-save-output-path"
            app.query_one("#output_name").value = "do-not-save-output-name"
            app.query_one("#format").value = "json"
            app.query_one("#split_by_level").value = True

            await pilot.click("#manage-recipes")
            await pilot.pause()
            app.screen.query_one("#recipe-name").value = "[red]literal[/red]"
            await pilot.click("#recipe-save")
            await pilot.pause()

            recipe = store.get("[red]literal[/red]")
            assert set(recipe) == RECIPE_FIELDS
            assert recipe == {
                "path": "incomplete-source",
                "pattern_mode": "custom",
                "log_pattern": "%msg%n",
                "logback_xml_path": "C:/logs/logback.xml",
                "start_date": "intentionally incomplete",
                "start_time": "08:30",
                "end_date": "2026-10-22",
                "end_time": "09:30",
                "keyword": "[red]literal[/red]",
                "ignore_case": True,
                "level_unrestricted": False,
                "levels": ["ERROR", "INFO", "TRACE"],
                "sort_by": "level",
                "display_mode": "full",
            }
            assert app.screen.query_one("#recipe-selection").value == "[red]literal[/red]"
            assert "[red]literal[/red]" in app.screen.query_one("#recipe-status").render().plain
            assert app.query_one("#output_path").value == "do-not-save-output-path"
            assert app.query_one("#output_name").value == "do-not-save-output-name"
            assert app.query_one("#format").value == "json"
            assert app.query_one("#split_by_level").value is True

    asyncio.run(run_check())


def test_apply_restores_saved_conditions_without_running_or_replacing_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-10-22 12:00:00.000 [main] ERROR com.test.App - retain this result\n",
        encoding="utf-8",
    )
    existing_result = analyze_logs(AnalysisRequest(path=str(logs)))
    existing_store = existing_result.matched_logs
    frozen_request = existing_result.analysis_request
    store = RecipeStore(tmp_path / "recipes.json")
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)
    monkeypatch.setattr(
        tui_module,
        "analyze_logs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("analysis must not run")),
    )
    monkeypatch.setattr(
        tui_module,
        "export_analysis_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("export must not run")),
    )
    saved = {
        "path": "saved/source",
        "pattern_mode": "custom",
        "log_pattern": "%msg%n",
        "logback_xml_path": "saved/logback.xml",
        "start_date": "2026-10-20",
        "start_time": "01:02",
        "end_date": "2026-10-21",
        "end_time": "03:04",
        "keyword": "saved keyword",
        "ignore_case": True,
        "level_unrestricted": False,
        "levels": ["WARN", "TRACE"],
        "sort_by": "level",
        "display_mode": "full",
    }
    store.save("Saved conditions", saved)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app._last_result = existing_result
            app._result_state = "complete"
            app.query_one("#output_path").value = "current-output-path"
            app.query_one("#output_name").value = "current-output-name"
            app.query_one("#format").value = "json"
            app.query_one("#split_by_level").value = True

            await pilot.click("#manage-recipes")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#recipe-selection").value = "Saved conditions"
            await pilot.click("#recipe-apply")
            await pilot.pause()

            assert app.screen is not screen
            assert app._last_result is existing_result
            assert app._result_state == "complete"
            _assert_recipe_form_matches(app, saved)
            assert app.query_one("#output_path").value == "current-output-path"
            assert app.query_one("#output_name").value == "current-output-name"
            assert app.query_one("#format").value == "json"
            assert app.query_one("#split_by_level").value is True
            assert existing_result.matched_logs is existing_store
            assert len(existing_store) == 1
            assert existing_result.analysis_request is frozen_request
            feedback = app.query_one("#recipe-feedback").render().plain
            assert "Saved conditions" in feedback
            assert app.query_one("#recipe-feedback").display is True
            assert "尚未執行分析" in feedback

    asyncio.run(run_check())


def test_legacy_custom_recipe_with_empty_pattern_keeps_xml_as_auto_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-10-22 12:00:00.000 INFO  [main] App: XML recipe pattern\n",
        encoding="utf-8",
    )
    xml_file = tmp_path / "config" / "logback.xml"
    xml_file.parent.mkdir()
    pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"
    xml_file.write_text(
        f'<configuration><property name="FILE_LOG_PATTERN" value="{pattern}"/></configuration>',
        encoding="utf-8",
    )
    store_path = tmp_path / "recipes.json"
    store = RecipeStore(store_path)
    store.save(
        "Legacy custom without pattern",
        {
            "path": str(logs),
            "pattern_mode": "custom",
            "log_pattern": "",
            "logback_xml_path": str(xml_file),
        },
    )
    original_recipe_bytes = store_path.read_bytes()
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            await pilot.click("#manage-recipes")
            await pilot.pause()
            app.screen.query_one("#recipe-selection").value = "Legacy custom without pattern"
            await pilot.click("#recipe-apply")
            await pilot.pause()

            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#log_pattern").value == ""
            assert app.query_one("#logback_xml_path").value == str(xml_file)
            assert store_path.read_bytes() == original_recipe_bytes

            await app.action_run_analysis()
            assert app._last_result is not None
            assert app._last_result.analysis_request.log_pattern == pattern

    asyncio.run(run_check())


def test_legacy_default_recipe_clears_inactive_pattern_fields_without_rewriting_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-10-22 12:00:00.000 [main] INFO  com.test.App - default recipe pattern\n",
        encoding="utf-8",
    )
    xml_file = logs / "logback.xml"
    xml_file.write_text(
        '<configuration><property name="FILE_LOG_PATTERN" value="%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"/></configuration>',
        encoding="utf-8",
    )
    store_path = tmp_path / "recipes.json"
    store = RecipeStore(store_path)
    store.save(
        "Legacy default",
        {
            "path": str(logs),
            "pattern_mode": "default",
            "log_pattern": "%msg%n",
            "logback_xml_path": str(xml_file),
        },
    )
    original_recipe_bytes = store_path.read_bytes()
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            existing_result = SimpleNamespace(matched_logs=SimpleNamespace(release=lambda: None))
            app._last_result = existing_result
            app._result_state = "complete"
            await pilot.click("#manage-recipes")
            await pilot.pause()
            app.screen.query_one("#recipe-selection").value = "Legacy default"
            await pilot.click("#recipe-apply")
            await pilot.pause()

            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#log_pattern").value == ""
            assert app.query_one("#logback_xml_path").value == ""
            assert "預設" in app.query_one("#recipe-feedback").render().plain
            assert app._recipe_autofill_source == tui_module.LogAnalyzerApp._normalize_recipe_source(str(logs))
            assert app._last_result is existing_result
            assert store_path.read_bytes() == original_recipe_bytes

            await app.action_run_analysis()
            assert app._last_result is not None
            assert app._last_result.analysis_request.log_pattern is None

    asyncio.run(run_check())


def test_unsupported_or_missing_pattern_mode_preserves_current_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    store.save("Unsupported mode", {"pattern_mode": "future-mode", "keyword": "kept"})
    store.save("Partial recipe", {"keyword": "partial"})
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app.query_one("#manual_pattern_override").value = True
            app.query_one("#log_pattern").value = "%msg%n"
            await pilot.click("#manage-recipes")
            await pilot.pause()
            app.screen.query_one("#recipe-selection").value = "Unsupported mode"
            await pilot.click("#recipe-apply")
            await pilot.pause()

            assert app.query_one("#manual_pattern_override").value is True
            assert "pattern_mode" in app.query_one("#recipe-feedback").render().plain

            await pilot.click("#manage-recipes")
            await pilot.pause()
            app.screen.query_one("#recipe-selection").value = "Partial recipe"
            await pilot.click("#recipe-apply")
            await pilot.pause()
            assert app.query_one("#manual_pattern_override").value is True
            assert app.query_one("#log_pattern").value == "%msg%n"

    asyncio.run(run_check())


def test_saved_recipes_survive_restart_and_delete_only_the_selected_recipe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store_path = tmp_path / "recipes.json"
    monkeypatch.setattr(
        tui_module,
        "RecipeStore",
        lambda: RecipeStore(store_path),
        raising=False,
    )

    async def save_recipe(app, pilot, name: str, path: str) -> None:
        app.query_one("#path").value = path
        await pilot.click("#manage-recipes")
        await pilot.pause()
        app.screen.query_one("#recipe-name").value = name
        await pilot.click("#recipe-save")
        await pilot.pause()
        await pilot.click("#recipe-close")
        await pilot.pause()

    async def run_check() -> None:
        first_app = LogAnalyzerApp()
        async with first_app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            await save_recipe(first_app, pilot, "Keep this", "first/source")
            await save_recipe(first_app, pilot, "Delete this", "second/source")

        second_app = LogAnalyzerApp()
        async with second_app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            await pilot.click("#manage-recipes")
            await pilot.pause()
            selector = second_app.screen.query_one("#recipe-selection")
            assert not isinstance(selector.value, str)
            selector.value = "Delete this"
            await pilot.click("#recipe-delete")
            await pilot.pause()

            assert not isinstance(second_app.screen.query_one("#recipe-selection").value, str)
            assert "Delete this" in second_app.screen.query_one("#recipe-status").render().plain
            reloaded = RecipeStore(store_path)
            assert reloaded.list_recipes() == {
                "Keep this": {
                    "path": "first/source",
                    "pattern_mode": "auto",
                    "log_pattern": "",
                    "logback_xml_path": "",
                    "start_date": "",
                    "start_time": "",
                    "end_date": "",
                    "end_time": "",
                    "keyword": "",
                    "ignore_case": True,
                    "level_unrestricted": False,
                    "levels": ["ERROR", "WARN", "INFO"],
                    "sort_by": "time",
                    "display_mode": "full",
                }
            }

    asyncio.run(run_check())


def test_modal_enter_does_not_run_actions_and_escape_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)
    monkeypatch.setattr(
        tui_module,
        "analyze_logs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("analysis must not run")),
    )
    monkeypatch.setattr(
        tui_module,
        "export_analysis_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("export must not run")),
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            base_screen = app.screen
            await pilot.click("#manage-recipes")
            await pilot.pause()
            recipe_screen = app.screen
            assert recipe_screen is not base_screen
            assert app.focused.id == "recipe-name"

            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is recipe_screen
            assert not store.path.exists()

            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is base_screen

    asyncio.run(run_check())


def test_corrupt_recipe_file_stays_unchanged_after_save_and_delete_attempts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store_path = tmp_path / "recipes.json"
    corrupt_bytes = b"{not-json"
    store_path.write_bytes(corrupt_bytes)
    monkeypatch.setattr(
        tui_module,
        "RecipeStore",
        lambda: RecipeStore(store_path),
        raising=False,
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.click("#manage-recipes")
            await pilot.pause()
            screen = app.screen
            assert "讀取配方失敗" in screen.query_one("#recipe-status").render().plain

            screen.query_one("#recipe-name").value = "Cannot replace corrupt file"
            await pilot.click("#recipe-save")
            await pilot.pause()
            assert "JSON" in screen.query_one("#recipe-status").render().plain
            assert store_path.read_bytes() == corrupt_bytes

            await pilot.click("#recipe-delete")
            await pilot.pause()
            assert "請先選擇" in screen.query_one("#recipe-status").render().plain
            assert store_path.read_bytes() == corrupt_bytes

    asyncio.run(run_check())


def test_recipe_modal_controls_are_scroll_reachable_in_narrow_and_wide_layouts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        tui_module,
        "RecipeStore",
        lambda: RecipeStore(tmp_path / "recipes.json"),
        raising=False,
    )

    async def run_check(size: tuple[int, int]) -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            base_screen = app.screen
            await pilot.click("#manage-recipes")
            await pilot.pause()
            screen = app.screen
            content = screen.query_one("#recipe-content")
            for selector in (
                "#recipe-selection",
                "#recipe-name",
                "#recipe-save",
                "#recipe-apply",
                "#recipe-delete",
                "#recipe-close",
            ):
                control = screen.query_one(selector)
                control.scroll_visible(animate=False)
                await pilot.pause()
                assert control.region.height > 0
                assert control.region.y >= 0
                assert control.region.y + control.region.height <= app.size.height
            assert content.max_scroll_y >= 0

            await pilot.click("#recipe-close")
            await pilot.pause()
            assert app.screen is base_screen

    asyncio.run(run_check((70, 18)))
    asyncio.run(run_check((150, 60)))


def test_invalid_saved_enums_are_ignored_with_a_warning_while_other_fields_apply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    store.save(
        "Partly unsupported",
        {
            "keyword": "preserved keyword",
            "sort_by": "unsupported-sort",
            "display_mode": "unsupported-mode",
            "levels": ["ERROR", "NOT_A_LEVEL"],
        },
    )
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.click("#manage-recipes")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#recipe-selection").value = "Partly unsupported"
            await pilot.click("#recipe-apply")
            await pilot.pause()

            assert app.query_one("#keyword").value == "preserved keyword"
            assert app.query_one("#sort_by").value == "time"
            assert len(app.query("#display_mode")) == 0
            assert app.query_one("#level_ERROR").value is True
            assert app.query_one("#level_WARN").value is False
            assert "sort_by" in app.query_one("#recipe-feedback").render().plain
            assert "levels" in app.query_one("#recipe-feedback").render().plain
            assert "display_mode" not in app.query_one("#recipe-feedback").render().plain

    asyncio.run(run_check())


def test_applied_default_pattern_is_preserved_until_source_path_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "recipe-source"
    logs.mkdir()
    pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
    (logs / "app.log").write_text(
        "2026-10-22 12:00:00.000 [main] ERROR com.test.App - default recipe pattern\n",
        encoding="utf-8",
    )
    xml_path = logs / "logback-spring.xml"
    xml_path.write_text(
        f'<configuration><property name="CONSOLE_LOG_PATTERN" value="{pattern}"/></configuration>',
        encoding="utf-8",
    )
    assert tui_module.find_best_logback_pattern(str(xml_path), str(logs)) is not None

    store = RecipeStore(tmp_path / "recipes.json")
    store.save(
        "Keep default mode",
        {
            "path": str(logs),
            "pattern_mode": "default",
            "log_pattern": "",
            "logback_xml_path": "",
        },
    )
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            await pilot.click("#manage-recipes")
            await pilot.pause()
            app.screen.query_one("#recipe-selection").value = "Keep default mode"
            await pilot.click("#recipe-apply")
            await pilot.pause()

            assert app.query_one("#manual_pattern_override").value is False
            assert app._recipe_autofill_source == tui_module.LogAnalyzerApp._normalize_recipe_source(str(logs))
            await app.action_run_analysis()
            await pilot.pause()

            assert app._last_result is not None, app._result_state
            assert app._last_result.analysis_request.log_pattern is None
            assert app.query_one("#manual_pattern_override").value is False

            other_logs = tmp_path / "other-source"
            other_logs.mkdir()
            (other_logs / "app.log").write_text(
                "2026-10-22 12:01:00.000 [main] ERROR com.test.App - new source\n",
                encoding="utf-8",
            )
            (other_logs / "logback.xml").write_text(xml_path.read_text(encoding="utf-8"), encoding="utf-8")
            app.query_one("#path").value = str(other_logs)
            await pilot.pause()
            assert app._recipe_autofill_source == ""
            app._autofill_logback_settings(str(other_logs))
            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#logback_xml_path").value == str(other_logs / "logback.xml")

    asyncio.run(run_check())
