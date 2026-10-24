from __future__ import annotations
from textual.events import Key, Paste
from textual.widgets import Input


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
