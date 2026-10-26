import asyncio
from dataclasses import FrozenInstanceError

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Static

from tui_waits import wait_for_layout_to_settle, wait_for_ui_state

from log_analyzer.presentation.tui.components.condition_overview import (
    ConditionItem,
    ConditionsOverview,
)


_KEYS = ("files", "levels", "time", "keyword", "pattern")
_LABELS = {
    "files": "Files",
    "levels": "Level",
    "time": "Time",
    "keyword": "Keyword",
    "pattern": "Pattern",
}


class _ConditionsOverviewHarness(App):
    def __init__(self) -> None:
        super().__init__()
        self.clear_requests: list[str] = []
        self.form_button_events: list[str | None] = []

    def compose(self) -> ComposeResult:
        yield Input(id="form-input")
        self.overview = ConditionsOverview()
        yield self.overview
        yield Button("提交", id="submit-form")

    def on_conditions_overview_clear_requested(
        self, event: ConditionsOverview.ClearRequested
    ) -> None:
        self.clear_requests.append(event.key)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.form_button_events.append(event.button.id)


def _updated_items() -> tuple[ConditionItem, ...]:
    return (
        ConditionItem("files", "Files", "app.log", True, "清除檔案"),
        ConditionItem("levels", "Level", "[bold]ERROR、WARN[/bold]", True),
        ConditionItem("time", "Time", "2026-10-01 至 2026-10-26", False),
        ConditionItem("keyword", "Keyword", "<literal> [red]字串", True),
        ConditionItem("pattern", "Pattern", "x" * 120, False),
    )


def _static_text(widget: Static) -> str:
    return widget.render().plain


async def _wait_for_button_origin(app, pilot, button: Button) -> None:
    def ready() -> bool:
        region = button.region
        return button.display and region.width > 0 and region.height > 0

    await wait_for_layout_to_settle(pilot, (button,), ready)


def test_condition_items_are_immutable_and_defaults_show_five_sensible_placeholders():
    """確認預設條件列完整、預設值清楚，且條件資料不可就地修改。"""
    async def run_check() -> None:
        app = _ConditionsOverviewHarness()
        async with app.run_test(size=(100, 36)) as pilot:
            overview = app.query_one("#condition-overview", ConditionsOverview)
            statics = [
                app.query_one(f"#condition-{key}", Static) for key in _KEYS
            ]
            buttons = [
                app.query_one(f"#clear-condition-{key}", Button) for key in _KEYS
            ]
            await wait_for_layout_to_settle(pilot, (overview, *statics, *buttons))

            title = app.query_one("#condition-overview-title", Static)
            assert _static_text(title) == "目前條件"
            assert [_static_text(widget).split("：", 1)[0] for widget in statics] == [
                _LABELS[key] for key in _KEYS
            ]
            assert all(
                _static_text(widget).split("：", 1)[1].strip()
                for widget in statics
            )
            assert all(button.disabled for button in buttons)

        with pytest.raises(FrozenInstanceError):
            ConditionItem("files", "Files", "app.log").value = "other.log"

    asyncio.run(run_check())


def test_set_items_updates_literal_text_tooltips_and_clear_state_without_reordering():
    """確認更新會保留五列順序，並以純文字顯示完整條件與清除狀態。"""
    async def run_check() -> None:
        app = _ConditionsOverviewHarness()
        async with app.run_test(size=(100, 36)) as pilot:
            overview = app.query_one("#condition-overview", ConditionsOverview)
            rows_before = tuple(overview.query(".condition-row"))
            buttons_before = tuple(
                app.query_one(f"#clear-condition-{key}", Button) for key in _KEYS
            )
            items = _updated_items()
            overview.set_items(items)
            await wait_for_layout_to_settle(
                pilot, (overview, *rows_before, *buttons_before)
            )
            focus_target = buttons_before[0]
            focus_target.focus()
            await wait_for_layout_to_settle(pilot, (focus_target,))
            assert app.focused is focus_target

            items = tuple(
                ConditionItem(
                    item.key,
                    item.label,
                    f"{item.value}!",
                    item.can_clear,
                    item.clear_label,
                )
                for item in items
            )
            overview.set_items(tuple(reversed(items)))

            assert tuple(overview.query(".condition-row")) == rows_before
            assert app.focused is focus_target
            assert [row.id for row in rows_before] == [
                f"condition-row-{key}" for key in _KEYS
            ]
            for item in items:
                summary = app.query_one(f"#condition-{item.key}", Static)
                button = app.query_one(f"#clear-condition-{item.key}", Button)
                expected = f"{item.label}：{item.value}"
                assert _static_text(summary) == expected
                assert summary.tooltip == expected
                assert str(button.label) == item.clear_label
                assert button.disabled is (not item.can_clear)
                assert button is buttons_before[_KEYS.index(item.key)]

        assert ConditionItem("files", "Files", "app.log").clear_label == "清除"

    asyncio.run(run_check())


def test_click_and_keyboard_clear_emit_one_key_without_bubbling_form_button_events():
    """確認滑鼠與鍵盤清除各送出一次條件訊息，且不冒泡成表單按鈕事件。"""
    async def run_check() -> None:
        app = _ConditionsOverviewHarness()
        async with app.run_test(size=(90, 36)) as pilot:
            overview = app.query_one("#condition-overview", ConditionsOverview)
            overview.set_items(_updated_items())

            files_button = app.query_one("#clear-condition-files", Button)
            await _wait_for_button_origin(app, pilot, files_button)
            assert files_button.region.height == 1
            assert await pilot.click(files_button, offset=(0, 0))
            await wait_for_ui_state(
                pilot,
                lambda: len(app.clear_requests) >= 1,
                "滑鼠清除訊息未送達",
            )
            assert app.clear_requests == ["files"]
            assert app.form_button_events == []

            levels_button = app.query_one("#clear-condition-levels", Button)
            await _wait_for_button_origin(app, pilot, levels_button)
            levels_button.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: len(app.clear_requests) >= 2,
                "鍵盤清除訊息未送達",
            )
            assert app.clear_requests == ["files", "levels"]
            assert app.form_button_events == []

    asyncio.run(run_check())


def test_disabled_clear_button_cannot_emit_and_resize_switches_columns():
    """確認停用的清除鈕不送出訊息，且元件會依自身寬度切換欄數。"""
    async def run_check() -> None:
        app = _ConditionsOverviewHarness()
        async with app.run_test(size=(54, 34)) as pilot:
            overview = app.query_one("#condition-overview", ConditionsOverview)
            long_items = tuple(
                ConditionItem(
                    item.key,
                    item.label,
                    item.value,
                    False,
                    item.clear_label,
                )
                for item in _updated_items()
            )
            overview.set_items(long_items)
            rows = [overview.query_one(f"#condition-row-{key}") for key in _KEYS]
            pattern_summary = app.query_one("#condition-pattern", Static)
            await wait_for_layout_to_settle(pilot, (overview, *rows, pattern_summary))
            assert overview.region.width < 60
            assert len({row.region.y for row in rows}) == len(_KEYS)
            assert pattern_summary.region.height > 1
            assert _static_text(pattern_summary) == (
                f"Pattern：{'x' * 120}"
            )

            disabled_button = app.query_one("#clear-condition-levels", Button)
            assert disabled_button.disabled
            await _wait_for_button_origin(app, pilot, disabled_button)
            assert disabled_button.region.height == 1
            await pilot.click(disabled_button, offset=(0, 0))
            await wait_for_layout_to_settle(pilot, (overview, disabled_button))
            assert app.clear_requests == []
            assert app.form_button_events == []

            await pilot.resize_terminal(100, 34)
            await wait_for_layout_to_settle(pilot, (overview, *rows))
            assert overview.region.width >= 60
            assert rows[0].region.y == rows[1].region.y
            assert rows[2].region.y == rows[3].region.y
            assert rows[4].region.y > rows[2].region.y
            assert all(
                row.region.x >= overview.content_region.x
                and row.region.x + row.region.width
                <= overview.content_region.x + overview.content_region.width
                for row in rows
            )

            await pilot.resize_terminal(54, 34)
            await wait_for_layout_to_settle(pilot, (overview, *rows))
            assert len({row.region.y for row in rows}) == len(_KEYS)

    asyncio.run(run_check())

