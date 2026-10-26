import asyncio

from textual.containers import VerticalScroll
from textual.events import Paste
from textual.widgets import Input, Static

from tui_waits import (
    wait_for_ui_state as _wait_for_ui_state,
    wait_for_scheduled_analysis as _wait_for_scheduled_analysis,
    wait_for_layout_to_settle as _wait_for_layout_to_settle,
)

from log_analyzer.domain.log_sources import select_log_source
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp


def _disable_recent_state(monkeypatch):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)


def _static_text(widget: Static) -> str:
    return widget.render().plain


async def _wait_for_source_preview(app, pilot, path) -> None:
    source = select_log_source(str(path))
    source_type = "單一檔案" if source.is_file else "資料夾"
    expected_summary = f"{source_type} · {len(source.filenames)} 個檔案"
    expected_filenames = "\n".join(source.filenames)

    await _wait_for_ui_state(
        pilot,
        lambda: (
            _static_text(app.query_one("#source-scope-summary", Static)) == expected_summary
            and _static_text(app.query_one("#source-scope-filenames", Static)) == expected_filenames
        ),
        f"source preview did not refresh for {path}",
    )
    await _wait_for_layout_to_settle(
        pilot,
        (
            app.query_one("#form-panel"),
            app.query_one("#source-scope-preview"),
            app.query_one("#source-scope-summary", Static),
            app.query_one("#source-scope-list", VerticalScroll),
            app.query_one("#source-scope-filenames", Static),
            app.query_one("#keyword", Input),
        ),
    )


async def _wait_for_picker_path(picker, pilot) -> Input:
    await _wait_for_ui_state(
        pilot,
        lambda: any(widget.is_mounted for widget in picker.query("#picker-path")),
        "source picker input did not mount",
    )
    path_input = picker.query_one("#picker-path", Input)
    confirm = picker.query_one("#picker-confirm")
    await _wait_for_ui_state(
        pilot,
        lambda: confirm.region.width > 0 and confirm.region.height > 0,
        "source picker confirm button was not laid out",
    )
    await _wait_for_layout_to_settle(
        pilot, (picker, picker.query_one("#picker-card"), path_input, confirm)
    )
    return path_input


async def _wait_for_source_picker(app, pilot):
    await _wait_for_ui_state(
        pilot,
        lambda: app.screen.__class__.__name__ == "SourcePickerScreen",
        "source picker screen did not open",
    )
    return app.screen


def test_tui_previews_supported_directory_and_single_file_before_analysis(tmp_path, monkeypatch):
    """情境：分析前應預覽資料夾或單檔的支援範圍。"""
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "audit.LOG").write_text("", encoding="utf-8")
    (log_dir / "capture.txt").write_text("", encoding="utf-8")
    (log_dir / "trace.out").write_text("", encoding="utf-8")
    (log_dir / "ignored.csv").write_text("", encoding="utf-8")
    nested = log_dir / "nested"
    nested.mkdir()
    (nested / "nested.log").write_text("", encoding="utf-8")
    (log_dir / "directory.log").mkdir()

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            path_input = app.query_one("#path", Input)
            path_input.value = str(log_dir)
            await _wait_for_source_preview(app, pilot, log_dir)

            summary = _static_text(app.query_one("#source-scope-summary", Static))
            hint = _static_text(app.query_one("#source-scope-hint", Static))
            filenames = _static_text(app.query_one("#source-scope-filenames", Static))
            assert "資料夾" in summary
            assert "3 個檔案" in summary
            assert "不包含子目錄" in hint
            assert filenames.splitlines() == ["audit.LOG", "capture.txt", "trace.out"]
            assert app._result_state == "idle"

            selected_file = log_dir / "audit.LOG"
            path_input.value = str(selected_file)
            await _wait_for_source_preview(app, pilot, selected_file)

            summary = _static_text(app.query_one("#source-scope-summary", Static))
            filenames = _static_text(app.query_one("#source-scope-filenames", Static))
            assert "單一檔案" in summary
            assert "1" in summary
            assert _static_text(app.query_one("#source-scope-hint", Static)) == ""
            assert filenames.splitlines() == ["audit.LOG"]
            assert path_input.value == str(selected_file)
            assert app._result_state == "idle"

    asyncio.run(run_check())


def test_tui_invalid_source_clears_previous_scope_preview(tmp_path, monkeypatch):
    """情境：來源無效時應清除前次來源預覽。"""
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "current.log").write_text("", encoding="utf-8")

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            path_input = app.query_one("#path", Input)
            path_input.value = str(log_dir)
            await _wait_for_source_preview(app, pilot, log_dir)
            assert "current.log" in _static_text(app.query_one("#source-scope-filenames", Static))

            invalid_path = tmp_path / "missing.log"
            path_input.value = str(invalid_path)
            await _wait_for_ui_state(
                pilot,
                lambda: (
                    "無效" in _static_text(app.query_one("#source-scope-summary", Static))
                    and _static_text(app.query_one("#source-scope-filenames", Static)) == ""
                ),
                "invalid source preview did not refresh",
            )

            summary = _static_text(app.query_one("#source-scope-summary", Static))
            filenames = _static_text(app.query_one("#source-scope-filenames", Static))
            assert "無效" in summary
            assert "current.log" not in filenames
            assert filenames == ""
            assert not app.query_one("#source-scope-list", VerticalScroll).display

    asyncio.run(run_check())


def test_tui_invalid_source_summary_keeps_markup_like_path_literal(tmp_path, monkeypatch):
    """情境：無效來源路徑中的標記語法應原樣顯示。"""
    _disable_recent_state(monkeypatch)
    invalid_path = tmp_path / "[bold]missing.log"

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(invalid_path)
            await _wait_for_ui_state(
                pilot,
                lambda: "[bold]" in _static_text(app.query_one("#source-scope-summary", Static)),
                "invalid source summary did not refresh",
            )

            summary = app.query_one("#source-scope-summary", Static)
            assert "[bold]" in summary.render().plain

    asyncio.run(run_check())


def test_tui_browse_source_accepts_a_file_and_a_directory(tmp_path, monkeypatch):
    """情境：來源瀏覽應同時接受檔案與資料夾。"""
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "selected.log"
    log_file.write_text("", encoding="utf-8")

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            assert await pilot.click("#browse_path")
            picker = await _wait_for_source_picker(app, pilot)
            assert picker.__class__.__name__ == "SourcePickerScreen"
            picker_path = await _wait_for_picker_path(picker, pilot)
            picker_path.value = str(log_file)
            confirm = picker.query_one("#picker-confirm")
            assert confirm.region.y + confirm.region.height <= 40, (
                confirm.region,
                picker.query_one("#picker-card").region,
            )
            assert await pilot.click("#picker-confirm")
            await _wait_for_ui_state(
                pilot,
                lambda: app.query_one("#path", Input).value == str(log_file),
                "selected file path was not returned to the form",
            )
            await _wait_for_source_preview(app, pilot, log_file)

            assert await pilot.click("#browse_path")
            picker = await _wait_for_source_picker(app, pilot)
            assert picker.__class__.__name__ == "SourcePickerScreen"
            picker_path = await _wait_for_picker_path(picker, pilot)
            picker_path.value = str(log_dir)
            confirm = picker.query_one("#picker-confirm")
            assert confirm.region.y + confirm.region.height <= 40, (
                confirm.region,
                picker.query_one("#picker-card").region,
            )
            assert await pilot.click("#picker-confirm")
            await _wait_for_ui_state(
                pilot,
                lambda: app.query_one("#path", Input).value == str(log_dir),
                "selected directory path was not returned to the form",
            )
            await _wait_for_source_preview(app, pilot, log_dir)
            assert "資料夾" in _static_text(app.query_one("#source-scope-summary", Static))

    asyncio.run(run_check())


def test_tui_source_scope_list_is_literal_bounded_and_scrollable(tmp_path, monkeypatch):
    """情境：檔名清單應原樣顯示、限制高度且可捲動。"""
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    names = ["[bold]literal.log"] + [f"long-{index:02d}-{'x' * 45}.LOG" for index in range(40)]
    for name in names:
        (log_dir / name).write_text("", encoding="utf-8")

    async def check_size(size: tuple[int, int]) -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            await _wait_for_source_preview(app, pilot, log_dir)

            scope_list = app.query_one("#source-scope-list", VerticalScroll)
            filenames = _static_text(app.query_one("#source-scope-filenames", Static))
            assert "[bold]literal.log" in filenames
            assert filenames.splitlines() == sorted(names)
            assert scope_list.region.height <= 8
            assert scope_list.max_scroll_y > 0
            scope_list.scroll_y = scope_list.max_scroll_y
            await pilot.pause()
            assert scope_list.scroll_y == scope_list.max_scroll_y
            assert scope_list.region.width <= size[0]
            assert app.query_one("#run").is_mounted

    asyncio.run(check_size((80, 32)))
    asyncio.run(check_size((180, 50)))


async def _select_scope(pilot, scope_list, path, *, scrollable, displayed=True):
    app = scope_list.app
    app.query_one("#path", Input).value = str(path)
    await _wait_for_source_preview(app, pilot, path)
    assert scope_list.display is displayed
    assert scope_list.focusable is scrollable
    assert (scope_list.max_scroll_y > 0) is scrollable


async def _resize_scope(pilot, scope_list, size, *, scrollable):
    await pilot.resize_terminal(*size)
    await pilot.pause()
    assert scope_list.focusable is scrollable
    assert (scope_list.max_scroll_y > 0) is scrollable


def test_source_scope_list_is_keyboard_focusable_only_when_scrollable(tmp_path, monkeypatch):
    """情境：來源清單只有需要捲動時才應取得鍵盤焦點。"""
    _disable_recent_state(monkeypatch)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    single_dir = tmp_path / "single"
    single_dir.mkdir()
    many_files = tmp_path / "many"
    many_files.mkdir()
    for index in range(12):
        (many_files / f"entry-{index:02d}.log").write_text("", encoding="utf-8")
    single_file = single_dir / "single.log"
    single_file.write_text("", encoding="utf-8")
    resize_dir = tmp_path / "resize"
    resize_dir.mkdir()
    for index in range(3):
        (resize_dir / f"resize-{index}-{'x' * 47}.log").write_text("", encoding="utf-8")

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            scope_list = app.query_one("#source-scope-list", VerticalScroll)
            await _select_scope(pilot, scope_list, empty_dir, scrollable=False, displayed=False)
            await _select_scope(pilot, scope_list, single_file, scrollable=False)
            await _select_scope(pilot, scope_list, many_files, scrollable=True)

            path_input = app.query_one("#path", Input)
            path_input.focus()
            for target in ("browse_path", "preview-format", "clear-condition-levels", "select-source-files"):
                await pilot.press("tab")
                assert app.focused is app.query_one(f"#{target}")
            await pilot.press("tab")
            assert app.focused is scope_list

            await _select_scope(pilot, scope_list, single_file, scrollable=False)
            await pilot.press("tab")
            assert app.focused is not scope_list
            await _select_scope(pilot, scope_list, many_files, scrollable=True)
            path_input.focus()
            for target in ("browse_path", "preview-format", "clear-condition-levels", "select-source-files"):
                await pilot.press("tab")
                assert app.focused is app.query_one(f"#{target}")
            await pilot.press("tab")
            assert app.focused is scope_list

            for _ in range(2):
                await _select_scope(pilot, scope_list, single_file, scrollable=False)
                await _select_scope(pilot, scope_list, many_files, scrollable=True)

            await _resize_scope(pilot, scope_list, (240, 44), scrollable=True)
            await _resize_scope(pilot, scope_list, (100, 40), scrollable=True)
            await _select_scope(pilot, scope_list, resize_dir, scrollable=False)
            await _resize_scope(pilot, scope_list, (120, 40), scrollable=True)
            await _resize_scope(pilot, scope_list, (80, 32), scrollable=False)
            await _resize_scope(pilot, scope_list, (120, 40), scrollable=True)

    asyncio.run(run_check())


def test_tui_paste_of_file_uri_keeps_file_scope_visible(tmp_path, monkeypatch):
    """情境：貼上 file URI 後應清楚保留單檔模式。"""
    _disable_recent_state(monkeypatch)
    log_file = tmp_path / "single.log"
    log_file.write_text("", encoding="utf-8")

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            path_input = app.query_one("#path", Input)
            path_input.focus()
            await pilot.pause()

            path_input.post_message(Paste(log_file.as_uri()))
            await _wait_for_source_preview(app, pilot, log_file)

            assert path_input.value == str(log_file)
            assert "單一檔案" in _static_text(app.query_one("#source-scope-summary", Static))
            assert _static_text(app.query_one("#source-scope-filenames", Static)).splitlines() == [log_file.name]

    asyncio.run(run_check())


def test_analysis_freezes_directory_file_list_when_execution_starts(tmp_path, monkeypatch):
    """情境：開始執行時應固定資料夾檔名清單。"""
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "first.log").write_text(
        "2026-10-23 12:00:00.123 [main] INFO  app.Logger - first\n",
        encoding="utf-8",
    )
    late_file = log_dir / "arrived-after-start.log"

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            await pilot.pause()
            analyze_request = app._analyze_request
            submitted_requests = []

            async def add_file_after_selection(request):
                submitted_requests.append(request)
                late_file.write_text(
                    "2026-10-23 12:00:01.123 [main] INFO  app.Logger - late\n",
                    encoding="utf-8",
                )
                return await analyze_request(request)

            monkeypatch.setattr(app, "_analyze_request", add_file_after_selection)
            await pilot.click("#run")
            await pilot.pause()
            await _wait_for_scheduled_analysis(app)

            assert len(submitted_requests) == 1
            assert submitted_requests[0].selected_files == ("first.log",)
            assert app._last_result is not None
            assert app._last_result.total_logs == 1

    asyncio.run(run_check())


def test_single_file_xml_autofill_ignores_conflicting_sibling_samples(tmp_path, monkeypatch):
    """情境：單檔自動 XML 評估應忽略同層衝突樣本。"""
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    xml_file = log_dir / "logback.xml"
    xml_file.write_text(
        """<configuration>
    <property name="CONSOLE_LOG_PATTERN" value="[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n"/>
    <property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/>
</configuration>
""",
        encoding="utf-8",
    )
    selected_file = log_dir / "selected.log"
    selected_file.write_text(
        "[2026-05-25 15:50:33.198] [INFO ] [main] YtMamApp.start(1) - selected\n",
        encoding="utf-8",
    )
    (log_dir / "sibling.log").write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: sibling one\n"
        "2026-05-25 15:50:34,198 INFO  [main] YtMamApp: sibling two\n",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(selected_file)
            await pilot.pause()
            submitted_requests = []
            analyze_request = app._analyze_request

            async def capture_request(request):
                submitted_requests.append(request)
                return await analyze_request(request)

            monkeypatch.setattr(app, "_analyze_request", capture_request)
            await pilot.click("#run")
            await pilot.pause()
            await _wait_for_scheduled_analysis(app)

            assert len(submitted_requests) == 1
            assert submitted_requests[0].selected_files == (selected_file.name,)
            assert submitted_requests[0].path == str(selected_file)
            assert "[%d{yyyy-MM-dd HH:mm:ss.SSS}]" in submitted_requests[0].log_pattern
            assert app._last_result is not None
            assert app._last_result.total_logs == 1

    asyncio.run(run_check())


def test_scope_preview_wraps_empty_single_and_many_files_without_overlap(tmp_path, monkeypatch):
    """情境：空、單檔與多檔預覽應換行且不遮蓋其他欄位。"""
    _disable_recent_state(monkeypatch)
    sources = []
    for source_name, file_count in (("empty", 0), ("single", 1), ("many", 7)):
        source_dir = tmp_path / source_name
        source_dir.mkdir()
        for index in range(file_count):
            (source_dir / f"source-{index}.log").write_text("", encoding="utf-8")
        sources.append((source_dir, file_count))

    async def check_size(size: tuple[int, int]) -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for source_dir, file_count in sources:
                app.query_one("#path", Input).value = str(source_dir)
                await _wait_for_source_preview(app, pilot, source_dir)

                preview = app.query_one("#source-scope-preview")
                summary = app.query_one("#source-scope-summary")
                file_list = app.query_one("#source-scope-list", VerticalScroll)
                keyword = app.query_one("#keyword", Input)
                assert preview.region.y <= summary.region.y
                assert preview.region.y + preview.region.height >= summary.region.y + summary.region.height
                assert keyword.region.y >= preview.region.y + preview.region.height
                if file_count:
                    assert file_list.display
                    assert preview.region.y + preview.region.height >= file_list.region.y + file_list.region.height
                    assert file_list.region.height <= 5
                else:
                    assert not file_list.display

                assert preview.region.x >= app.query_one("#form-panel").content_region.x
                assert preview.region.x + preview.region.width <= (
                    app.query_one("#form-panel").content_region.x
                    + app.query_one("#form-panel").content_region.width
                )
                if file_count == 7:
                    assert file_list.max_scroll_y > 0

    for size in ((80, 32), (100, 40), (120, 40), (160, 44), (240, 44)):
        asyncio.run(check_size(size))
