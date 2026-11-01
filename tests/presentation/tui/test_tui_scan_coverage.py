import asyncio
import os
from io import StringIO
from pathlib import Path

import pytest
from rich.cells import cell_len
from rich.console import Console
from textual.containers import VerticalScroll
from textual.widgets import RichLog, Static

from tui_waits import wait_for_layout_to_settle, wait_for_ui_state

from log_analyzer.application.analysis.service import AnalysisRequest, analyze_logs
from log_analyzer.domain.logback.xml import assess_logback_patterns
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.screens.pattern_confirmation import LogbackPatternConfirmationScreen
from log_analyzer.presentation.tui.views import build_dashboard_view
from tui_harnesses import ScreenHarness


PATTERN = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n"
LINE = "2026-10-24 10:00:00.000 [worker] ERROR Service - failed"


def _capture(app, name):
    directory = os.environ.get("TUI_CAPTURE_DIR")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        app.save_screenshot(filename=name + ".svg", path=directory)


@pytest.mark.parametrize("size", [(72, 28), (72, 45), (160, 45)], ids=['viewport-72x28', 'viewport-72x45', 'viewport-160x45'])
def test_scan_coverage_fits_result_width(tmp_path, monkeypatch, size):
    """情境：各視窗尺寸下掃描覆蓋摘要應留在結果寬度內。"""
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    (tmp_path / "app.log").write_text("unrecognized [red]literal[/red]\n" + LINE + "\n", encoding="utf-8")
    result = analyze_logs(AnalysisRequest(str(tmp_path)))

    async def check():
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            box = app.query_one("#result-box", RichLog)
            app._display_analysis_result(box, result)
            await wait_for_ui_state(pilot, lambda: bool(box.lines), "result did not render")
            await wait_for_layout_to_settle(pilot, (box,))
            width = box.content_size.width
            rendered = Console(width=width, file=StringIO(), record=True, legacy_windows=False)
            rendered.print(build_dashboard_view(result, compact=True))
            text = rendered.export_text()
            assert "逐檔解析診斷" in text
            assert "unrecognized [red]literal[/red]" in text
            assert all(cell_len(line) <= width for line in text.splitlines()), (
                width, [(cell_len(line), line) for line in text.splitlines() if cell_len(line) > width]
            )
            assert box.region.right <= app.screen.region.right
            index = next(i for i, line in enumerate(box.lines) if "逐檔解析診斷" in line.text)
            box.scroll_to(y=index, animate=False)
            await wait_for_layout_to_settle(pilot, (box,))
            if size[1] >= 40:
                assert box.content_size.height >= 6
            _capture(app, f"coverage-{size[0]}")

    try:
        asyncio.run(check())
    finally:
        result.matched_logs.release()


@pytest.mark.parametrize("size", [(72, 28), (160, 45)], ids=['viewport-72x28', 'viewport-160x45'])
def test_pattern_dialog_shows_per_file_scores_and_keyboard_cancel(tmp_path, size):
    """情境：寬窄 Pattern 視窗應顯示逐檔評分且可鍵盤取消。"""
    xml = tmp_path / "logback.xml"
    xml.write_text(f'<configuration><property name="PATTERN" value="{PATTERN}"/></configuration>', encoding="utf-8")
    (tmp_path / "a.log").write_text((LINE + "\n") * 50, encoding="utf-8")
    (tmp_path / "b.log").write_text("different format\n" * 50, encoding="utf-8")
    assessment = assess_logback_patterns([str(xml)], str(tmp_path))

    async def check():
        app = ScreenHarness()
        async with app.run_test(size=size) as pilot:
            dialog = LogbackPatternConfirmationScreen(assessment)
            await app.push_screen(dialog)
            container = dialog.query_one("#pattern-candidates", VerticalScroll)
            await wait_for_layout_to_settle(pilot, (container,))
            labels = "\n".join(widget.render().plain for widget in dialog.query(Static))
            assert "a.log：命中 25/25" in labels
            assert "b.log：命中 0/25" in labels
            assert container.region.right <= app.screen.region.right
            _capture(app, f"pattern-files-{size[0]}")
            await pilot.press("escape")
            await wait_for_ui_state(pilot, lambda: app.screen is not dialog, "dialog did not close")

    asyncio.run(check())
