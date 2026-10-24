import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from log_analyzer.domain.log_sources import select_log_source
from log_analyzer.presentation.tui.source_scope import SourceScopePreview


class _SourceScopeHarness(App):
    def compose(self) -> ComposeResult:
        yield Input(id="before")
        yield SourceScopePreview()
        yield Input(id="keyword")


def _text(widget: Static) -> str:
    return widget.render().plain


def test_source_scope_summary_separates_folder_hint_and_single_file(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / "first.log").write_text("", encoding="utf-8")
    (folder / "second.txt").write_text("", encoding="utf-8")
    nested = folder / "nested"
    nested.mkdir()
    (nested / "nested.log").write_text("", encoding="utf-8")
    selected_file = folder / "first.log"

    async def run_check() -> None:
        app = _SourceScopeHarness()
        async with app.run_test() as pilot:
            await pilot.pause()
            preview = app.query_one("#source-scope-preview", SourceScopePreview)
            preview.show_source(select_log_source(str(folder)))
            await pilot.pause()

            summary = _text(app.query_one("#source-scope-summary", Static))
            hint = _text(app.query_one("#source-scope-hint", Static))
            filenames = _text(app.query_one("#source-scope-filenames", Static))
            assert "資料夾" in summary
            assert "2 個檔案" in summary
            assert hint == "不包含子目錄"
            assert filenames.splitlines() == ["first.log", "second.txt"]

            preview.show_source(select_log_source(str(selected_file)))
            await pilot.pause()

            summary = _text(app.query_one("#source-scope-summary", Static))
            assert "單一檔案" in summary
            assert "1 個檔案" in summary
            assert _text(app.query_one("#source-scope-hint", Static)) == ""
            assert _text(app.query_one("#source-scope-filenames", Static)).splitlines() == ["first.log"]

    asyncio.run(run_check())


def test_source_scope_empty_and_invalid_states_are_explicit_and_clear_stale_data(tmp_path):
    empty_folder = tmp_path / "empty"
    empty_folder.mkdir()
    invalid_message = "[bold]missing.log"

    async def run_check() -> None:
        app = _SourceScopeHarness()
        async with app.run_test() as pilot:
            await pilot.pause()
            preview = app.query_one("#source-scope-preview", SourceScopePreview)
            preview.show_source(select_log_source(str(empty_folder)))
            await pilot.pause()

            summary = _text(app.query_one("#source-scope-summary", Static))
            hint = _text(app.query_one("#source-scope-hint", Static))
            assert "資料夾" in summary
            assert "0 個檔案" in summary
            assert "不包含子目錄" in hint
            assert "沒有支援的日誌檔案" in hint
            assert _text(app.query_one("#source-scope-filenames", Static)) == ""
            assert not app.query_one("#source-scope-list", VerticalScroll).display

            populated = tmp_path / "populated"
            populated.mkdir()
            (populated / "current.log").write_text("", encoding="utf-8")
            preview.show_source(select_log_source(str(populated)))
            await pilot.pause()
            preview.show_invalid(invalid_message)
            await pilot.pause()

            summary_widget = app.query_one("#source-scope-summary", Static)
            assert "來源無效" in _text(summary_widget)
            assert "[bold]missing.log" in _text(summary_widget)
            assert _text(app.query_one("#source-scope-filenames", Static)) == ""
            assert _text(app.query_one("#source-scope-hint", Static)) == ""
            assert not app.query_one("#source-scope-list", VerticalScroll).display

    asyncio.run(run_check())


@pytest.mark.parametrize("size", ((80, 24), (120, 44), (160, 44)))
def test_source_scope_panel_wraps_names_and_keeps_keyword_below_panel(tmp_path, size):
    folder = tmp_path / "logs"
    folder.mkdir()
    long_filename = "[bold]" + "長" * 65 + ".log"
    (folder / long_filename).write_text("", encoding="utf-8")

    async def run_check() -> None:
        app = _SourceScopeHarness()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            preview = app.query_one("#source-scope-preview", SourceScopePreview)
            preview.show_source(select_log_source(str(folder)))
            await pilot.pause()

            summary = app.query_one("#source-scope-summary", Static)
            scope_list = app.query_one("#source-scope-list", VerticalScroll)
            filenames = app.query_one("#source-scope-filenames", Static)
            keyword = app.query_one("#keyword", Input)
            assert "本次分析範圍" in preview.border_title
            assert preview.region.width <= size[0]
            assert summary.region.x >= preview.region.x
            assert scope_list.region.x + scope_list.region.width <= preview.region.x + preview.region.width
            assert keyword.region.y >= preview.region.y + preview.region.height + 1
            assert _text(filenames) == long_filename
            assert filenames.region.width <= scope_list.content_region.width

    asyncio.run(run_check())


def test_source_scope_list_is_focusable_only_when_it_scrolls_and_updates_after_resize(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    for index in range(3):
        filename = f"{index}-{'長' * 65}.log"
        (folder / filename).write_text("", encoding="utf-8")
    source = select_log_source(str(folder))

    async def run_check() -> None:
        app = _SourceScopeHarness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            preview = app.query_one("#source-scope-preview", SourceScopePreview)
            preview.show_source(source)
            await pilot.pause()
            scope_list = app.query_one("#source-scope-list", VerticalScroll)
            assert scope_list.max_scroll_y > 0
            assert scope_list.focusable

            app.query_one("#before", Input).focus()
            await pilot.press("tab")
            assert app.focused is scope_list

            await pilot.resize_terminal(160, 44)
            await pilot.pause()
            assert scope_list.max_scroll_y == 0
            assert not scope_list.focusable

            app.query_one("#before", Input).focus()
            await pilot.press("tab")
            assert app.focused is app.query_one("#keyword", Input)

            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            assert scope_list.max_scroll_y > 0
            assert scope_list.focusable

    asyncio.run(run_check())
