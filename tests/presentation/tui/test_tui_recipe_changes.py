import asyncio

from log_analyzer.infrastructure.recipe_store import RecipeStore
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp
from tui_waits import (
    wait_for_button_ready,
    wait_for_layout_to_settle,
    wait_for_ui_state,
)


def _recipe(keyword: str = "saved keyword") -> dict[str, object]:
    return {
        "path": "saved/source",
        "pattern_mode": "auto",
        "log_pattern": "",
        "logback_xml_path": "",
        "start_date": "",
        "start_time": "",
        "end_date": "",
        "end_time": "",
        "keyword": keyword,
        "ignore_case": False,
        "level_unrestricted": True,
        "levels": ["ERROR", "WARN", "INFO", "DEBUG", "TRACE"],
        "sort_by": "time",
        "display_mode": "full",
    }


async def _open_recipe_manager(app, pilot):
    """開啟配方管理器並等待其主要控制項就緒。

    Args:
        app: 正在執行的主畫面。
        pilot: Textual 測試操作器。

    Returns:
        配方管理器畫面。
    """
    await wait_for_button_ready(app, pilot, "#manage-recipes")
    await pilot.click("#manage-recipes")
    await wait_for_ui_state(
        pilot,
        lambda: len(app.screen.query("#recipe-selection")) == 1,
        "recipe manager did not open",
    )
    return app.screen


async def _apply_recipe(app, pilot, name: str):
    """從配方管理器載入指定配方。

    Args:
        app: 正在執行的主畫面。
        pilot: Textual 測試操作器。
        name: 要載入的配方名稱。

    Returns:
        None: 等待主畫面重新顯示。
    """
    screen = await _open_recipe_manager(app, pilot)
    screen.query_one("#recipe-selection").value = name
    await wait_for_button_ready(app, pilot, "#recipe-apply")
    await pilot.click("#recipe-apply")
    await wait_for_ui_state(
        pilot,
        lambda: app.screen is not screen,
        "selected recipe was not applied",
    )


def test_loaded_recipe_feedback_tracks_changes_and_restoration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    store.save("Saved search", _recipe())
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await _apply_recipe(app, pilot, "Saved search")

            feedback = app.query_one("#recipe-feedback").render().plain
            assert "Saved search" in feedback
            assert "已修改" not in feedback

            keyword = app.query_one("#keyword")
            keyword.value = "changed keyword"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "editing a loaded recipe did not mark it modified",
            )

            keyword.value = "saved keyword"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" not in app.query_one("#recipe-feedback").render().plain,
                "restoring the loaded recipe values did not clear modified state",
            )

            ignore_case = app.query_one("#ignore_case")
            ignore_case.value = True
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "changing a recipe checkbox did not mark it modified",
            )
            ignore_case.value = False
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" not in app.query_one("#recipe-feedback").render().plain,
                "restoring a recipe checkbox did not clear modified state",
            )

            sort_by = app.query_one("#sort_by")
            sort_by.value = "level"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "changing a recipe selection did not mark it modified",
            )
            sort_by.value = "time"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" not in app.query_one("#recipe-feedback").render().plain,
                "restoring a recipe selection did not clear modified state",
            )

            level_trace = app.query_one("#level_TRACE")
            level_trace.value = False
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "changing a recipe level did not mark it modified",
            )
            level_trace.value = True
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" not in app.query_one("#recipe-feedback").render().plain,
                "restoring a recipe level did not clear modified state",
            )

            app.query_one("#clear-condition-keyword").focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "clearing a condition overview field did not mark it modified",
            )
            keyword.value = "saved keyword"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" not in app.query_one("#recipe-feedback").render().plain,
                "restoring a cleared condition did not clear modified state",
            )

            app.query_one("#output_name").value = "export-only-change"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" not in app.query_one("#recipe-feedback").render().plain,
                "changing an export-only field marked the recipe modified",
            )

    asyncio.run(run_check())


def test_save_as_rejects_collisions_and_update_targets_loaded_recipe(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    store.save("Loaded recipe", _recipe())
    store.save("Other recipe", _recipe("other value"))
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await _apply_recipe(app, pilot, "Loaded recipe")
            app.query_one("#keyword").value = "edited current conditions"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "loaded recipe did not become modified",
            )

            screen = await _open_recipe_manager(app, pilot)
            screen.query_one("#recipe-selection").value = "Other recipe"
            screen.query_one("#recipe-name").value = "Other recipe"
            assert "Loaded recipe" in screen.query_one("#recipe-active").render().plain
            await wait_for_button_ready(app, pilot, "#recipe-save")
            await pilot.click("#recipe-save")
            await wait_for_ui_state(
                pilot,
                lambda: "已存在" in screen.query_one("#recipe-status").render().plain,
                "save-as collision did not require an explicit choice",
            )
            collision_status = screen.query_one("#recipe-status").render().plain
            assert "Other recipe" in collision_status
            assert "Loaded recipe" in collision_status
            assert store.get("Other recipe")["keyword"] == "other value"

            await wait_for_button_ready(app, pilot, "#recipe-update")
            await pilot.click("#recipe-update")
            await wait_for_ui_state(
                pilot,
                lambda: "已更新目前配方" in screen.query_one("#recipe-status").render().plain,
                "explicit update did not finish",
            )
            assert store.get("Loaded recipe")["keyword"] == "edited current conditions"
            assert store.get("Other recipe")["keyword"] == "other value"
            assert "已修改" not in app.query_one("#recipe-feedback").render().plain

    asyncio.run(run_check())


def test_saved_recipe_is_tracked_and_deleting_it_clears_saved_claim(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            expected_saved_state = app._capture_recipe_state()
            screen = await _open_recipe_manager(app, pilot)
            screen.query_one("#recipe-name").value = "New recipe"
            await wait_for_button_ready(app, pilot, "#recipe-save")
            await pilot.click("#recipe-save")
            await wait_for_ui_state(
                pilot,
                lambda: "New recipe" in app.query_one("#recipe-feedback").render().plain,
                "saving a new recipe did not update the main feedback",
            )
            assert "已修改" not in app.query_one("#recipe-feedback").render().plain
            assert store.get("New recipe") == expected_saved_state

            await wait_for_button_ready(app, pilot, "#recipe-close")
            await pilot.click("#recipe-close")
            await wait_for_ui_state(
                pilot,
                lambda: app.screen is not screen,
                "recipe manager did not close",
            )
            app.query_one("#keyword").value = "unsaved change"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "editing after save did not mark the recipe modified",
            )

            screen = await _open_recipe_manager(app, pilot)
            screen.query_one("#recipe-selection").value = "New recipe"
            await wait_for_button_ready(app, pilot, "#recipe-delete")
            await pilot.click("#recipe-delete")
            await wait_for_ui_state(
                pilot,
                lambda: "已刪除" in screen.query_one("#recipe-status").render().plain,
                "active recipe was not deleted",
            )
            feedback = app.query_one("#recipe-feedback").render().plain
            assert "目前配方：「New recipe」" not in feedback
            assert "已刪除" in feedback
            assert screen.query_one("#recipe-update").disabled
            assert store.list_recipes() == {}

    asyncio.run(run_check())


def test_load_and_update_failures_keep_the_active_dirty_recipe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store_path = tmp_path / "recipes.json"
    store = RecipeStore(store_path)
    store.save("Loaded recipe", _recipe())
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await _apply_recipe(app, pilot, "Loaded recipe")
            app.query_one("#keyword").value = "unsaved value"
            await wait_for_ui_state(
                pilot,
                lambda: "已修改" in app.query_one("#recipe-feedback").render().plain,
                "loaded recipe did not become modified",
            )

            def fail_get(_name: str) -> dict[str, object]:
                raise OSError("read failed")

            monkeypatch.setattr(store, "get", fail_get)
            screen = await _open_recipe_manager(app, pilot)
            screen.query_one("#recipe-selection").value = "Loaded recipe"
            await wait_for_button_ready(app, pilot, "#recipe-apply")
            await pilot.click("#recipe-apply")
            await wait_for_ui_state(
                pilot,
                lambda: "read failed" in screen.query_one("#recipe-status").render().plain,
                "failed recipe load was not reported",
            )
            assert app.screen is screen
            assert "已修改" in app.query_one("#recipe-feedback").render().plain

            def fail_save(_name: str, _state: object) -> None:
                raise OSError("disk full")

            monkeypatch.setattr(store, "save", fail_save)
            await wait_for_button_ready(app, pilot, "#recipe-update")
            await pilot.click("#recipe-update")
            await wait_for_ui_state(
                pilot,
                lambda: "disk full" in screen.query_one("#recipe-status").render().plain,
                "failed recipe update was not reported",
            )
            assert store_path.exists()
            assert RecipeStore(store_path).get("Loaded recipe")["keyword"] == "saved keyword"
            assert "已修改" in app.query_one("#recipe-feedback").render().plain

    asyncio.run(run_check())


def test_legacy_default_recipe_is_clean_after_canonicalization(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = RecipeStore(tmp_path / "recipes.json")
    legacy = _recipe()
    legacy["pattern_mode"] = "default"
    legacy["log_pattern"] = "legacy pattern"
    legacy["logback_xml_path"] = "legacy/logback.xml"
    store.save("Legacy default", legacy)
    monkeypatch.setattr(tui_module, "RecipeStore", lambda: store, raising=False)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(160, 44)) as pilot:
            await _apply_recipe(app, pilot, "Legacy default")
            feedback = app.query_one("#recipe-feedback").render().plain
            assert "Legacy default" in feedback
            assert "已修改" not in feedback
            assert app.query_one("#log_pattern").value == ""
            assert app.query_one("#logback_xml_path").value == ""

    asyncio.run(run_check())


def test_recipe_manager_actions_fit_and_scroll_at_requested_sizes(tmp_path, monkeypatch):
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
            screen = await _open_recipe_manager(app, pilot)
            content = screen.query_one("#recipe-content")
            actions = screen.query_one("#recipe-actions")
            assert ("recipe-compact" in actions.classes) == (size[0] < 112)
            expected_layout = "grid" if size[0] < 112 else "horizontal"
            assert expected_layout in str(actions.styles.layout)
            for selector in (
                "#recipe-selection",
                "#recipe-name",
                "#recipe-active",
                "#recipe-save",
                "#recipe-update",
                "#recipe-apply",
                "#recipe-delete",
                "#recipe-close",
            ):
                control = screen.query_one(selector)
                control.scroll_visible(animate=False)
                await wait_for_layout_to_settle(
                    pilot,
                    (control, content),
                    lambda: (
                        control.region.height > 0
                        and control.region.y >= 0
                        and control.region.bottom <= app.size.height
                        and control.region.x >= content.region.x
                        and control.region.right <= content.region.right
                    ),
                )
                assert control.region.height > 0
                assert control.region.y >= 0
                assert control.region.bottom <= app.size.height
                assert control.region.x >= content.region.x
                assert control.region.right <= content.region.right
            assert content.max_scroll_y >= 0

            buttons = tuple(screen.query("#recipe-actions Button"))
            assert len(buttons) == 5
            for index, left in enumerate(buttons):
                for right in buttons[index + 1 :]:
                    overlap = (
                        left.region.x < right.region.right
                        and left.region.right > right.region.x
                        and left.region.y < right.region.bottom
                        and left.region.bottom > right.region.y
                    )
                    assert not overlap
            if size[0] >= 112:
                row_buttons = sorted(buttons, key=lambda button: button.region.x)
                assert all(
                    left.region.y == right.region.y
                    and right.region.x - left.region.right >= 1
                    for left, right in zip(row_buttons, row_buttons[1:])
                )
            assert screen.query_one("#recipe-update").region.width >= 14

    for size in ((160, 44), (80, 24), (64, 18)):
        asyncio.run(run_check(size))
