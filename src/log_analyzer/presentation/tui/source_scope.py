from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Static

from ...domain.log_sources import LogSourceSelection


class _SourceScopeList(VerticalScroll):
    def allow_focus(self) -> bool:
        return self.display and self.max_scroll_y > 0


class SourceScopePreview(Container):
    DEFAULT_CSS = """
    SourceScopePreview {
        width: 1fr;
        height: auto;
        min-width: 0;
        margin-bottom: 1;
        padding: 0 1;
        border: round #334155;
        background: #0b1220;
    }

    #source-scope-summary {
        width: 1fr;
        min-width: 0;
        height: auto;
        min-height: 1;
        color: #e2e8f0;
        text-style: bold;
        overflow-x: hidden;
        text-wrap: wrap;
    }

    #source-scope-hint {
        width: 1fr;
        min-width: 0;
        height: auto;
        color: #94a3b8;
        overflow-x: hidden;
        text-wrap: wrap;
    }

    #source-scope-list {
        width: 1fr;
        min-width: 0;
        height: auto;
        min-height: 0;
        max-height: 5;
        display: none;
        padding: 0;
        overflow-x: hidden;
        overflow-y: auto;
    }

    #source-scope-filenames {
        width: 1fr;
        min-width: 0;
        height: auto;
        color: #cbd5e1;
        overflow-x: hidden;
        text-wrap: wrap;
    }
    """

    def __init__(self, *, id: str = "source-scope-preview") -> None:
        super().__init__(id=id)
        self.border_title = "本次分析範圍"

    def compose(self) -> ComposeResult:
        yield Static("尚未選擇 Log 來源", id="source-scope-summary", markup=False)
        yield Static("", id="source-scope-hint", markup=False)
        with _SourceScopeList(id="source-scope-list"):
            yield Static("", id="source-scope-filenames", markup=False)

    def show_source(self, source: LogSourceSelection) -> None:
        source_type = "單一檔案" if source.is_file else "資料夾"
        self.query_one("#source-scope-summary", Static).update(
            f"{source_type} · {len(source.filenames)} 個檔案"
        )

        hint = ""
        if not source.is_file:
            hint = "不包含子目錄"
            if not source.filenames:
                hint += " · 沒有支援的日誌檔案"
        hint_widget = self.query_one("#source-scope-hint", Static)
        hint_widget.update(hint)
        hint_widget.display = bool(hint)

        self.query_one("#source-scope-filenames", Static).update("\n".join(source.filenames))
        self.query_one("#source-scope-list", _SourceScopeList).display = bool(source.filenames)

    def show_invalid(self, message: str) -> None:
        self.query_one("#source-scope-summary", Static).update(f"來源無效：{message}")
        self.query_one("#source-scope-hint", Static).update("")
        self.query_one("#source-scope-hint", Static).display = False
        self.query_one("#source-scope-filenames", Static).update("")
        self.query_one("#source-scope-list", _SourceScopeList).display = False
