from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.events import Key, Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static
from log_analyzer.infrastructure.recipe_store import RecipeStore


@dataclass(frozen=True)
class RecipeTrackingContext:
    """主畫面提供給管理器的目前配方與追蹤通知函式。"""

    active_recipe_name: str | None = None
    on_saved: Callable[[str, Mapping[str, object]], None] | None = None
    on_deleted: Callable[[str], None] | None = None


class RecipeManagerScreen(ModalScreen[None]):
    def __init__(
        self,
        store: RecipeStore,
        capture_recipe: Callable[[], dict[str, object]],
        apply_recipe: Callable[[str, Mapping[str, object]], str | None],
        *,
        tracking: RecipeTrackingContext | None = None,
    ) -> None:
        """建立配方管理器，保留原三個位置參數供既有呼叫端使用。

        Args:
            store: 讀取、寫入與刪除配方的儲存區。
            capture_recipe: 擷取目前表單配方欄位的函式。
            apply_recipe: 將指定配方套用至主畫面的函式。
            tracking: 目前配方名稱及存檔、刪除通知函式。

        Returns:
            None: 建立尚未掛載的管理器畫面。
        """
        self.store = store
        self.capture_recipe = capture_recipe
        self.apply_recipe = apply_recipe
        tracking_context = tracking or RecipeTrackingContext()
        self._active_recipe_name = tracking_context.active_recipe_name
        self.on_recipe_saved = tracking_context.on_saved
        self.on_recipe_deleted = tracking_context.on_deleted
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
            yield Static("", id="recipe-active", markup=False)
            with Container(id="recipe-actions"):
                yield Button("另存新配方", id="recipe-save")
                yield Button("更新目前配方", id="recipe-update")
                yield Button("套用", variant="primary", id="recipe-apply")
                yield Button("刪除", id="recipe-delete")
                yield Button("關閉", id="recipe-close")

    def on_mount(self) -> None:
        self._refresh_recipes()
        self._sync_action_layout()
        self.query_one("#recipe-name", Input).focus()

    def on_resize(self, _event: Resize) -> None:
        self._sync_action_layout()

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
        elif event.button.id == "recipe-update":
            self._update_active_recipe()
        elif event.button.id == "recipe-apply":
            self._apply_selected_recipe()
        elif event.button.id == "recipe-delete":
            self._delete_selected_recipe()
        elif event.button.id == "recipe-close":
            self.dismiss()

    def _save_recipe(self) -> None:
        name = self.query_one("#recipe-name", Input).value
        try:
            recipes = self.store.list_recipes()
            normalized_name = name.strip()
            if normalized_name in recipes:
                if self._active_recipe_name in self._recipes:
                    message = (
                        f"配方「{normalized_name}」已存在，未另存。更新按鈕只會覆寫目前配方"
                        f"「{self._active_recipe_name}」；若要更新「{normalized_name}」，請先載入它。"
                    )
                else:
                    message = (
                        f"配方「{normalized_name}」已存在，未另存。請改名，或先載入"
                        f"「{normalized_name}」再使用更新按鈕。"
                    )
                self._set_status(
                    message
                )
                return
            state = self.capture_recipe()
            self.store.save(normalized_name, state)
        except Exception as exc:  # noqa: BLE001 - persistence failures must be visible
            self._set_status(str(exc))
            return

        self._active_recipe_name = normalized_name
        self._notify_recipe_saved(normalized_name, state)
        if self._refresh_recipes(normalized_name):
            self._set_status(f"已另存新配方：「{normalized_name}」")

    def _update_active_recipe(self) -> None:
        """只更新目前表單追蹤的已載入或已儲存配方。

        Returns:
            None: 成功時更新儲存內容與追蹤快照，失敗時保留現況。
        """
        name = self._active_recipe_name
        if name is None:
            self._set_status("請先載入或儲存配方，才能更新目前配方。")
            return

        try:
            recipes = self.store.list_recipes()
            if name not in recipes:
                self._active_recipe_name = None
                self._sync_update_button()
                if self.on_recipe_deleted is not None:
                    self.on_recipe_deleted(name)
                self._set_status(f"目前配方「{name}」已不存在，無法更新。")
                return
            state = self.capture_recipe()
            self.store.save(name, state)
        except Exception as exc:  # noqa: BLE001 - persistence failures must be visible
            self._set_status(str(exc))
            return

        self._notify_recipe_saved(name, state)
        if self._refresh_recipes(name):
            self._set_status(f"已更新目前配方：「{name}」")

    def _notify_recipe_saved(
        self, name: str, state: Mapping[str, object]
    ) -> None:
        """將成功寫入的配方通知主畫面。

        Args:
            name: 已寫入的配方名稱。
            state: 實際寫入的表單條件。

        Returns:
            None: 有設定通知函式時呼叫一次。
        """
        self._sync_update_button()
        if self.on_recipe_saved is not None:
            self.on_recipe_saved(name, state)

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
        self._active_recipe_name = name
        self._sync_update_button()
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

        if self._active_recipe_name == name:
            self._active_recipe_name = None
            if self.on_recipe_deleted is not None:
                self.on_recipe_deleted(name)

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
        self._sync_update_button()
        return loaded

    def _sync_update_button(self) -> None:
        """依照目前配方是否仍存在，更新明確更新按鈕的可用狀態。

        Returns:
            None: 配方尚未載入或已不存在時停用按鈕。
        """
        button = self.query_one("#recipe-update", Button)
        active_name = self._active_recipe_name
        active_is_saved = active_name is not None and active_name in self._recipes
        button.disabled = not active_is_saved
        target = self.query_one("#recipe-active", Static)
        if active_is_saved:
            target.update(
                f"更新目標：「{active_name}」（與清單目前選取項目無關）。"
            )
        else:
            target.update("目前沒有已載入或已儲存的配方可供更新。")

    def _sync_action_layout(self) -> None:
        """依管理器寬度切換單列或兩欄按鈕排列。

        Returns:
            None: 窄畫面使用兩欄，寬畫面使用單列。
        """
        if not self.is_mounted:
            return
        actions = self.query_one("#recipe-actions", Container)
        compact = self.size.width < 112
        if compact:
            actions.add_class("recipe-compact")
            actions.styles.layout = "grid"
            actions.styles.grid_size_columns = 2
            actions.styles.grid_size_rows = 0
            actions.styles.grid_columns = "1fr 1fr"
            actions.styles.grid_gutter_horizontal = 1
            actions.styles.grid_gutter_vertical = 0
        else:
            actions.remove_class("recipe-compact")
            actions.styles.layout = "horizontal"
        buttons = tuple(actions.query(Button))
        for index, button in enumerate(buttons):
            button.styles.width = "1fr"
            button.styles.min_width = 14
            right_margin = 1 if not compact and index < len(buttons) - 1 else 0
            button.styles.margin = (0, right_margin, 0, 0)

    def _set_status(self, message: str) -> None:
        self.query_one("#recipe-status", Static).update(message)
