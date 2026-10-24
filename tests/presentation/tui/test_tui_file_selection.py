import asyncio
import os
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, SelectionList, Static

from tui_waits import wait_for_button_ready, wait_for_layout_to_settle, wait_for_scheduled_analysis, wait_for_ui_state

from log_analyzer.domain.log_sources import select_log_source
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.screens.file_selection import SourceFileSelectionScreen


class SelectionHarness(App):
    def __init__(self, source):
        self.source = source
        self.chosen = None
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("source")

    def on_mount(self):
        self.push_screen(SourceFileSelectionScreen(self.source, self.source.filenames), self._chosen)

    def _chosen(self, names):
        self.chosen = names


def test_file_search_preserves_hidden_choices_and_keyboard_selection(tmp_path):
    for name, size in (("a.log", 3), ("b.txt", 5), ("c.out", 7)):
        (tmp_path / name).write_text("x" * size, encoding="utf-8")

    async def check():
        app = SelectionHarness(select_log_source(str(tmp_path)))
        async with app.run_test(size=(72, 30)) as pilot:
            dialog = app.screen
            search = dialog.query_one("#source-file-search", Input)
            search.value = "a.log"
            await wait_for_ui_state(pilot, lambda: dialog._visible == ("a.log",), "file search did not update")
            dialog.query_one("#source-file-none").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: dialog._selected == {"b.txt", "c.out"}, "visible deselect lost hidden choices")
            assert "12.0 B" in dialog.query_one("#source-file-summary", Static).render().plain
            search.value = ""
            await wait_for_ui_state(pilot, lambda: len(dialog._visible) == 3, "clear search did not restore list")
            assert set(dialog.query_one("#source-file-list", SelectionList).selected) == {"b.txt", "c.out"}
            await wait_for_button_ready(app, pilot, "#source-file-none")
            dialog.query_one("#source-file-none").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: not dialog._selected, "deselect did not finish")
            assert dialog.query_one("#source-file-apply").disabled
            options = dialog.query_one("#source-file-list", SelectionList)
            options.highlighted = 0
            options.focus()
            await pilot.press("space")
            await wait_for_ui_state(pilot, lambda: dialog._selected == {"a.log"}, "space did not select file")
            await wait_for_button_ready(app, pilot, "#source-file-apply")
            dialog.query_one("#source-file-apply").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: app.chosen == ("a.log",), "selection was not applied")

    asyncio.run(check())


@pytest.mark.parametrize("size", [(64, 18), (80, 32), (160, 40)])
def test_file_selection_actions_fit_and_cancel_preserves_source(tmp_path, size):
    (tmp_path / "app.log").write_text("sample", encoding="utf-8")
    source = select_log_source(str(tmp_path))

    async def check():
        app = SelectionHarness(source)
        async with app.run_test(size=size) as pilot:
            dialog = app.screen
            widgets = [dialog.query_one(f"#source-file-{name}") for name in ("search", "list", "all", "none", "cancel", "apply")]
            await wait_for_layout_to_settle(pilot, widgets)
            for widget in widgets:
                assert widget.region.bottom <= app.screen.region.bottom, (size, widget.id, widget.region)
                assert widget.region.right <= app.screen.region.right, (size, widget.id, widget.region)
                assert widget.is_on_screen
            directory = os.environ.get("TUI_CAPTURE_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                app.save_screenshot(filename=f"source-select-{size[0]}x{size[1]}.svg", path=directory)
            await pilot.press("escape")
            await wait_for_ui_state(pilot, lambda: app.screen is not dialog, "selection did not cancel")
            assert app.chosen is None
            assert source.filenames == ("app.log",)

    asyncio.run(check())


def test_selected_files_are_shared_by_xml_assessment_and_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "good.log").write_text("2026-10-24 10:00:00.000 [main] INFO Service - selected\n", encoding="utf-8")
    (logs / "bad.txt").write_text("different format\n" * 5, encoding="utf-8")
    xml = tmp_path / "logback.xml"
    xml.write_text('<configuration><pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n</pattern></configuration>', encoding="utf-8")

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one("#path", Input).value = str(logs)
            app.query_one("#logback_xml_path", Input).value = str(xml)
            await wait_for_ui_state(pilot, lambda: app._file_selection_state().path == str(logs.resolve()), "source did not refresh")
            app.query_one("#select-source-files").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: isinstance(app.screen, SourceFileSelectionScreen), "file selector did not open")
            dialog = app.screen
            dialog.query_one("#source-file-search", Input).value = "bad.txt"
            await wait_for_ui_state(pilot, lambda: dialog._visible == ("bad.txt",), "file search did not update")
            dialog.query_one("#source-file-none").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: dialog._selected == {"good.log"}, "bad file was not deselected")
            dialog.query_one("#source-file-apply").focus()
            await pilot.press("enter")
            await wait_for_ui_state(pilot, lambda: app.screen is not dialog, "selection did not apply")
            assert app.query_one("#source-scope-filenames", Static).render().plain == "good.log"
            assert "命中 1/1" in app.query_one("#logback-xml-status", Static).render().plain
            await wait_for_button_ready(app, pilot)
            app.query_one("#run").focus()
            await pilot.press("enter")
            await wait_for_scheduled_analysis(app)
            assert app._last_result.analysis_request.selected_files == ("good.log",)
            assert set(app._last_result.source_states) == {"good.log"}
            assert app._last_result.matched_occurrences == 1

    asyncio.run(check())
