from __future__ import annotations
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Input, SelectionList, Static
from log_analyzer.domain.log_sources import LogSourceSelection
from log_analyzer.presentation.source_labels import source_file_sizes, format_size


class SourceFileSelectionScreen(ModalScreen[tuple[str, ...] | None]):
    BINDINGS = [("escape", "cancel", "取消")]
    DEFAULT_CSS = """
    SourceFileSelectionScreen { align: center middle; background: rgba(15, 23, 42, 0.78); }
    #source-selection-card { width: 92%; height: 90%; padding: 1 2; border: round #38bdf8; background: #0b1220; }
    #source-selection-card.short { height: 100%; padding: 0 1; }
    #source-file-title { height: 1; text-style: bold; }
    #source-file-search { height: 3; }
    #source-file-summary { height: auto; max-height: 2; }
    #source-file-list { height: 1fr; min-height: 1; }
    #source-file-actions { dock: bottom; height: 6; layout: grid; grid-size: 2 2; grid-columns: 1fr 1fr; grid-rows: 3 3; grid-gutter: 0 1; }
    #source-file-actions Button { width: 1fr; min-width: 0; margin: 0; }
    """

    def __init__(self, source: LogSourceSelection, selected: tuple[str, ...]) -> None:
        self.source = source
        self._selected = set(selected) & set(source.filenames)
        self._visible: tuple[str, ...] = ()
        self._sizes = source_file_sizes(source)
        super().__init__()

    def compose(self) -> ComposeResult:
        with Container(id="source-selection-card"):
            yield Static("選擇本次分析檔案", id="source-file-title")
            yield Input(placeholder="搜尋檔名；空白鍵勾選", id="source-file-search")
            yield Static("", id="source-file-summary", markup=False)
            yield SelectionList[str](id="source-file-list")
            with Container(id="source-file-actions"):
                yield Button("全選列出", id="source-file-all")
                yield Button("取消列出", id="source-file-none")
                yield Button("取消", id="source-file-cancel")
                yield Button("套用選取", id="source-file-apply", variant="primary")

    def on_mount(self) -> None:
        self._refresh_options(capture=False)
        self.query_one("#source-file-search", Input).focus()
        self._sync_layout()

    def on_resize(self) -> None:
        if self.is_mounted:
            self._sync_layout()

    def _sync_layout(self) -> None:
        self.query_one("#source-selection-card").set_class(
            self.size.height < 30, "short"
        )

    def _capture_selection(self) -> None:
        self._selected.difference_update(self._visible)
        self._selected.update(
            self.query_one("#source-file-list", SelectionList).selected
        )

    def _refresh_options(self, capture: bool = True) -> None:
        if capture:
            self._capture_selection()
        needle = self.query_one("#source-file-search", Input).value.strip().casefold()
        self._visible = tuple(
            name for name in self.source.filenames if needle in name.casefold()
        )
        options = self.query_one("#source-file-list", SelectionList)
        options.clear_options()
        options.add_options(
            [
                (
                    Text(
                        f"{name}  ({format_size(self._sizes[name]) if self._sizes[name] is not None else '大小未知'})"
                    ),
                    name,
                    name in self._selected,
                )
                for name in self._visible
            ]
        )
        self._update_summary()

    def _update_summary(self) -> None:
        size = sum(self._sizes[name] or 0 for name in self._selected)
        unknown = sum(self._sizes[name] is None for name in self._selected)
        suffix = f"；另 {unknown} 個大小未知" if unknown else ""
        self.query_one("#source-file-summary", Static).update(
            f"已選 {len(self._selected)}/{len(self.source.filenames)} 個 · {format_size(size)}{suffix}；列出 {len(self._visible)} 個"
        )
        self.query_one("#source-file-apply", Button).disabled = not self._selected

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "source-file-search" and self.is_mounted:
            self._refresh_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "source-file-search":
            event.stop()
            self.query_one("#source-file-list").focus()

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        if event.selection_list.id == "source-file-list":
            self._capture_selection()
            self._update_summary()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "source-file-cancel":
            self.action_cancel()
        elif button_id in {"source-file-all", "source-file-none"}:
            self._capture_selection()
            if button_id == "source-file-all":
                self._selected.update(self._visible)
            else:
                self._selected.difference_update(self._visible)
            self._refresh_options(capture=False)
        elif button_id == "source-file-apply":
            self._capture_selection()
            if self._selected:
                self.dismiss(
                    tuple(
                        name for name in self.source.filenames if name in self._selected
                    )
                )
