from __future__ import annotations

from collections.abc import Callable, Mapping

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from ..infrastructure.recipe_store import RecipeStore


class RecipeManagerScreen(ModalScreen[None]):
    def __init__(
        self,
        store: RecipeStore,
        capture_recipe: Callable[[], dict[str, object]],
        apply_recipe: Callable[[str, Mapping[str, object]], str | None],
    ) -> None:
        self.store = store
        self.capture_recipe = capture_recipe
        self.apply_recipe = apply_recipe
        self._recipes: dict[str, dict[str, object]] = {}
        super().__init__()

    def compose(self) -> ComposeResult:
        with (
            Container(id="recipe-dialog"),
            Container(id="recipe-dialog-card"),
            ScrollableContainer(id="recipe-content"),
        ):
            yield Static("管理分析配方", id="recipe-title")
            yield Label("已儲存的配方")
            yield Select([], prompt="選擇配方", allow_blank=True, id="recipe-selection")
            yield Label("配方名稱")
            yield Input(placeholder="輸入配方名稱", id="recipe-name")
            yield Static("", id="recipe-status", markup=False)
            with Container(id="recipe-actions"):
                yield Button("儲存目前條件", id="recipe-save")
                yield Button("套用", variant="primary", id="recipe-apply")
                yield Button("刪除", id="recipe-delete")
                yield Button("關閉", id="recipe-close")

    def on_mount(self) -> None:
        self._refresh_recipes()
        self.query_one("#recipe-name", Input).focus()

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "recipe-save":
            self._save_recipe()
        elif event.button.id == "recipe-apply":
            self._apply_selected_recipe()
        elif event.button.id == "recipe-delete":
            self._delete_selected_recipe()
        elif event.button.id == "recipe-close":
            self.dismiss()

    def _save_recipe(self) -> None:
        name = self.query_one("#recipe-name", Input).value
        try:
            self.store.save(name, self.capture_recipe())
        except Exception as exc:  # noqa: BLE001 - persistence failures must be visible
            self._set_status(str(exc))
            return

        if self._refresh_recipes(name.strip()):
            self._set_status(f"已儲存配方：{name.strip()}")

    def _apply_selected_recipe(self) -> None:
        name = self._selected_name()
        if name is None:
            self._set_status("請先選擇要套用的配方")
            return

        try:
            recipe = self.store.get(name)
            message = self.apply_recipe(name, recipe)
        except Exception as exc:  # noqa: BLE001 - keep the modal open on read/apply errors
            self._set_status(str(exc))
            return

        if message:
            self._set_status(message)
        self.dismiss()

    def _delete_selected_recipe(self) -> None:
        name = self._selected_name()
        if name is None:
            self._set_status("請先選擇要刪除的配方")
            return

        try:
            self.store.delete(name)
        except Exception as exc:  # noqa: BLE001 - corrupt/read-only files must remain visible
            self._set_status(str(exc))
            return

        if self._refresh_recipes():
            self._set_status(f"已刪除配方：{name}")

    def _selected_name(self) -> str | None:
        value = self.query_one("#recipe-selection", Select).value
        return value if isinstance(value, str) else None

    def _refresh_recipes(self, selected_name: str | None = None) -> bool:
        loaded = True
        try:
            self._recipes = self.store.list_recipes()
        except Exception as exc:  # noqa: BLE001 - store failures must remain visible
            self._recipes = {}
            self._set_status(f"讀取配方失敗：{exc}")
            loaded = False

        selection = self.query_one("#recipe-selection", Select)
        selection.set_options([(Text(name), name) for name in self._recipes])
        if selected_name in self._recipes:
            selection.value = selected_name
        else:
            selection.clear()
        return loaded

    def _set_status(self, message: str) -> None:
        self.query_one("#recipe-status", Static).update(message)
