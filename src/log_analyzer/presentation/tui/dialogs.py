from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Iterable

from rich.text import Text
from textual.containers import Container
from textual.events import Key, Paste
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, Static
from textual.containers import VerticalScroll

from ...domain.log_sources import select_log_source
from ...domain.logback_xml import LogbackPatternAssessment, LogbackPatternCandidate
from ...infrastructure.paths import (
    get_system_root_path,
    inspect_directory_path,
    inspect_file_path,
)


class ExportConflictScreen(ModalScreen[str | None]):
    """列出匯出衝突路徑，讓使用者選擇改名、覆蓋或取消。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, paths: Iterable[str]) -> None:
        self.paths = tuple(paths)
        super().__init__()

    def compose(self):
        with Container(id="export-conflict"), Container(id="export-conflict-card"):
            yield Static("輸出檔案已存在", id="export-conflict-title")
            yield Static(
                "請改名、確認覆蓋，或取消本次匯出。",
                id="export-conflict-message",
                markup=False,
            )
            with VerticalScroll(id="export-conflict-scroll"):
                yield Static(
                    "\n".join(self.paths),
                    id="export-conflict-paths",
                    markup=False,
                )
            with Container(id="export-conflict-actions"):
                yield Button("改名", id="export-conflict-rename")
                yield Button("覆蓋", variant="warning", id="export-conflict-overwrite")
                yield Button("取消", id="export-conflict-cancel")

    def on_mount(self) -> None:
        self.query_one("#export-conflict-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "export-conflict-rename": "rename",
            "export-conflict-overwrite": "overwrite",
            "export-conflict-cancel": None,
        }
        if event.button.id in actions:
            self.dismiss(actions[event.button.id])


class LogbackPatternConfirmationScreen(ModalScreen[LogbackPatternCandidate | None]):
    """展示不確定的 Logback Pattern 與樣本，等待使用者明確選擇。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, assessment: LogbackPatternAssessment) -> None:
        self.assessment = assessment
        super().__init__()

    def compose(self):
        message = (
            f"{self.assessment.reason} 抽樣 {self.assessment.checked} 個非空行；"
            "最多取前 50 行，堆疊續行可能影響比例。請選擇可用的 Pattern。"
        )
        with Container(id="pattern-confirmation"), Container(id="pattern-confirmation-card"):
            yield Static("確認 Logback Pattern", id="pattern-confirmation-title")
            yield Static(message, id="pattern-confirmation-message", markup=False)
            with VerticalScroll(id="pattern-candidates"):
                for index, candidate in enumerate(self.assessment.candidates):
                    with Container(classes="pattern-candidate"):
                        yield Static(
                            f"{candidate.name[:80]} · {candidate.xml_path[:300]} · 命中 {candidate.matches}/{candidate.checked}",
                            classes="pattern-candidate-title",
                            markup=False,
                        )
                        yield Static(
                            f"Pattern：{candidate.pattern[:500]}",
                            classes="pattern-candidate-pattern",
                            markup=False,
                        )
                        if candidate.error:
                            yield Static(
                                f"不可選：{candidate.error[:200]}",
                                classes="pattern-candidate-error",
                                markup=False,
                            )
                        else:
                            yield Button(
                                f"使用此 Pattern（命中 {candidate.matches}/{candidate.checked}）",
                                id=f"pattern-select-{index}",
                                variant="primary",
                            )
                        for preview in candidate.previews:
                            label = "命中樣本" if preview.fields is not None else "未命中樣本"
                            yield Static(
                                f"{label}：{preview.raw}",
                                classes="pattern-candidate-preview",
                                markup=False,
                            )
                            if preview.fields is not None:
                                parsed = " | ".join(
                                    f"{name}={value}"
                                    for name, value in preview.fields.items()
                                )
                                yield Static(
                                    f"解析：{parsed}",
                                    classes="pattern-candidate-preview",
                                    markup=False,
                                )
            with Container(id="pattern-confirmation-actions"):
                yield Button("取消分析", id="pattern-cancel")

    def on_mount(self) -> None:
        self.query_one("#pattern-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "pattern-cancel":
            self.dismiss(None)
            return
        if not button_id.startswith("pattern-select-"):
            return
        try:
            index = int(button_id.removeprefix("pattern-select-"))
            candidate = self.assessment.candidates[index]
        except (ValueError, IndexError):
            return
        if candidate.error is None:
            self.dismiss(candidate)


class FolderOnlyDirectoryTree(DirectoryTree):
    """只保留資料夾節點，避免選到檔案"""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir()]


class ShortcutInput(Input):
    """補上符合一般編輯習慣的輸入框快捷鍵"""

    def on_key(self, event: Key) -> None:
        if event.key == "ctrl+a":
            self.select_all()
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+u":
            self.value = ""
            event.prevent_default()
            event.stop()

    def _on_paste(self, event: Paste) -> None:
        handle_path_paste = getattr(self.app, "handle_path_paste", None)
        if callable(handle_path_paste) and handle_path_paste(self, event.text):
            event.text = ""
            event.stop()


class DirectoryPickerScreen(ModalScreen[str | None]):
    """資料夾選擇彈窗，支援樹狀瀏覽與手動輸入"""

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
        self._selected_path: str | None = None
        super().__init__()

    def compose(self):
        root_path, status_message = self._pick_tree_root()
        with Container(id="directory-picker"), Container(id="picker-card"):
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


class FilePickerScreen(ModalScreen[str | None]):
    """檔案選擇彈窗，支援樹狀瀏覽與手動輸入"""

    def __init__(
        self,
        initial_path: str,
        title: str,
        hint: str,
        confirm_label: str,
        allow_directory: bool = False,
    ) -> None:
        self.initial_path = initial_path
        self.title_text = title
        self.hint_text = hint
        self.confirm_label = confirm_label
        self.allow_directory = allow_directory
        self._selected_path: str | None = None
        super().__init__()

    def compose(self):
        root_path, status_message = self._pick_tree_root()
        with Container(id="directory-picker"), Container(id="picker-card"):
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
        if self.allow_directory:
            color, message = self._source_status(selected_path)
        else:
            color, message, _, _ = inspect_file_path(selected_path)
        self.query_one("#picker-status", Static).update(self._status_text(message, color))

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        picker_path = self.query_one("#picker-path", Input)
        selected_path = str(event.path)
        picker_path.value = selected_path
        if self.allow_directory:
            self._selected_path = selected_path
            color, message = self._source_status(selected_path)
            self.query_one("#picker-status", Static).update(self._status_text(message, color))
        else:
            self.query_one("#picker-status", Static).update(
                self._status_text("請選擇檔案，或手動輸入完整檔案路徑", "yellow")
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
        if self.allow_directory:
            try:
                source = select_log_source(selected_path)
            except (OSError, ValueError) as exc:
                self.query_one("#picker-status", Static).update(self._status_text(str(exc), "red"))
                return
            self.dismiss(source.path)
            return

        color, message, is_valid, _ = inspect_file_path(selected_path)
        if not is_valid:
            self.query_one("#picker-status", Static).update(self._status_text(message, color))
            return
        self.dismiss(os.path.abspath(selected_path))

    @staticmethod
    def _source_status(selected_path: str) -> tuple[str, str]:
        try:
            source = select_log_source(selected_path)
        except (OSError, ValueError) as exc:
            return "red", str(exc)
        if source.is_file:
            return "green", "可使用的 Log 檔案"
        return "green", f"可使用的資料夾（不含子目錄，{len(source.filenames)} 個支援檔案）"

    def _pick_tree_root(self) -> tuple[Path, str]:
        root_path = get_system_root_path()
        return root_path, ""

    def _status_text(self, message: str, color: str) -> Text:
        text = Text()
        text.append(message, style=color)
        return text


class SourcePickerScreen(FilePickerScreen):
    """可選擇單一 Log 檔案或資料夾的來源選擇器。"""

    def __init__(self, initial_path: str, title: str, hint: str, confirm_label: str) -> None:
        super().__init__(initial_path, title, hint, confirm_label, allow_directory=True)
