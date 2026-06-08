from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from rich.text import Text
from textual.containers import Container
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, Static

from ..infrastructure.paths import (
    get_system_root_path,
    inspect_directory_path,
    inspect_file_path,
)


class FolderOnlyDirectoryTree(DirectoryTree):
    """只保留資料夾節點，避免選到檔案。"""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir()]


class ShortcutInput(Input):
    """補上符合一般編輯習慣的輸入框快捷鍵。"""

    def on_key(self, event: Key) -> None:
        if event.key == "ctrl+a":
            self.select_all()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+u":
            self.value = ""
            event.prevent_default()
            event.stop()


class DirectoryPickerScreen(ModalScreen[Optional[str]]):
    """資料夾選擇彈窗，支援樹狀瀏覽與手動輸入。"""

    def __init__(
        self,
        initial_path: str,
        title: str,
        hint: str,
        confirm_label: str,
        require_writable: bool = False,
    ) -> None:
        self.initial_path = initial_path
        self.title_text = title
        self.hint_text = hint
        self.confirm_label = confirm_label
        self.require_writable = require_writable
        self._selected_path: Optional[str] = None
        super().__init__()

    def compose(self):
        root_path, status_message = self._pick_tree_root()
        with Container(id="directory-picker"):
            with Container(id="picker-card"):
                yield Static(self.title_text, id="picker-title")
                yield Static(self.hint_text, id="picker-hint")
                yield FolderOnlyDirectoryTree(root_path, id="picker-tree")
                with Container(id="picker-manual"):
                    yield Label("手動輸入")
                    yield ShortcutInput(value=self.initial_path or str(root_path), id="picker-path")
                yield Static(status_message, id="picker-status")
                with Container(id="picker-actions"):
                    yield Button(self.confirm_label, variant="primary", id="picker-confirm")
                    yield Button("取消", id="picker-cancel")

    def on_mount(self) -> None:
        self.query_one("#picker-path", Input).focus()

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        selected_path = str(event.path)
        self._selected_path = selected_path
        picker_path = self.query_one("#picker-path", Input)
        picker_path.value = selected_path
        color, message, _, _ = inspect_directory_path(selected_path, require_writable=self.require_writable)
        self.query_one("#picker-status", Static).update(self._status_text(message, color))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-cancel":
            self.dismiss(None)
            return
        if event.button.id == "picker-confirm":
            self._confirm_selection()

    def _confirm_selection(self) -> None:
        picker_path = self.query_one("#picker-path", Input)
        selected_path = picker_path.value.strip() or self._selected_path or ""
        color, message, is_valid, _ = inspect_directory_path(selected_path, require_writable=self.require_writable)
        if not is_valid:
            self.query_one("#picker-status", Static).update(self._status_text(message, color))
            return
        self.dismiss(os.path.abspath(selected_path))

    def _pick_tree_root(self) -> tuple[Path, str]:
        root_path = get_system_root_path()
        return root_path, ""

    def _status_text(self, message: str, color: str) -> Text:
        text = Text()
        text.append(message, style=color)
        return text


class FilePickerScreen(ModalScreen[Optional[str]]):
    """檔案選擇彈窗，支援樹狀瀏覽與手動輸入。"""

    def __init__(
        self,
        initial_path: str,
        title: str,
        hint: str,
        confirm_label: str,
    ) -> None:
        self.initial_path = initial_path
        self.title_text = title
        self.hint_text = hint
        self.confirm_label = confirm_label
        self._selected_path: Optional[str] = None
        super().__init__()

    def compose(self):
        root_path, status_message = self._pick_tree_root()
        with Container(id="directory-picker"):
            with Container(id="picker-card"):
                yield Static(self.title_text, id="picker-title")
                yield Static(self.hint_text, id="picker-hint")
                yield DirectoryTree(root_path, id="picker-tree")
                with Container(id="picker-manual"):
                    yield Label("手動輸入")
                    yield ShortcutInput(value=self.initial_path or str(root_path), id="picker-path")
                yield Static(status_message, id="picker-status")
                with Container(id="picker-actions"):
                    yield Button(self.confirm_label, variant="primary", id="picker-confirm")
                    yield Button("取消", id="picker-cancel")

    def on_mount(self) -> None:
        self.query_one("#picker-path", Input).focus()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        selected_path = str(event.path)
        self._selected_path = selected_path
        picker_path = self.query_one("#picker-path", Input)
        picker_path.value = selected_path
        color, message, _, _ = inspect_file_path(selected_path)
        self.query_one("#picker-status", Static).update(self._status_text(message, color))

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        picker_path = self.query_one("#picker-path", Input)
        picker_path.value = str(event.path)
        self.query_one("#picker-status", Static).update(
            self._status_text("請選擇檔案，或手動輸入完整檔案路徑。", "yellow")
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-cancel":
            self.dismiss(None)
            return
        if event.button.id == "picker-confirm":
            self._confirm_selection()

    def _confirm_selection(self) -> None:
        picker_path = self.query_one("#picker-path", Input)
        selected_path = picker_path.value.strip() or self._selected_path or ""
        color, message, is_valid, _ = inspect_file_path(selected_path)
        if not is_valid:
            self.query_one("#picker-status", Static).update(self._status_text(message, color))
            return
        self.dismiss(os.path.abspath(selected_path))

    def _pick_tree_root(self) -> tuple[Path, str]:
        root_path = get_system_root_path()
        return root_path, ""

    def _status_text(self, message: str, color: str) -> Text:
        text = Text()
        text.append(message, style=color)
        return text
