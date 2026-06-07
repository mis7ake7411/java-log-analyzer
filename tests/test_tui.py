import asyncio
from pathlib import Path
from importlib.metadata import version as package_version
from datetime import date

from log_analyzer.tui import (
    DirectoryPickerScreen,
    LogAnalyzerApp,
    get_default_end_date_text,
    get_default_end_time_text,
    get_default_output_name_text,
    get_default_start_date_text,
    get_default_start_time_text,
    get_package_version,
    get_system_root_path,
    parse_datetime_range_inputs,
)


def test_get_system_root_path_returns_current_drive_root():
    expected_root = Path(Path.cwd().anchor or "/")

    assert get_system_root_path() == expected_root


def test_directory_picker_root_starts_from_system_root():
    screen = DirectoryPickerScreen(
        initial_path=r"C:\not-used",
        title="選擇 Log 目錄",
        hint="",
        confirm_label="使用此路徑",
    )

    root_path, message = screen._pick_tree_root()

    assert root_path == Path(Path.cwd().anchor or "/")
    assert message == ""


def test_clear_keyword_action_empties_keyword_input():
    class FakeInput:
        def __init__(self) -> None:
            self.value = "RuntimeException"
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    app = LogAnalyzerApp()
    fake_input = FakeInput()
    app.query_one = lambda *_args, **_kwargs: fake_input  # type: ignore[assignment]

    app.action_clear_keyword()

    assert fake_input.value == ""
    assert fake_input.focused is True


def test_get_package_version_matches_installed_package():
    assert get_package_version() == package_version("java-log-analyzer")


def test_default_datetime_field_texts():
    assert get_default_start_date_text() == date.today().isoformat()
    assert get_default_start_time_text() == "00:00"
    assert get_default_end_date_text() == ""
    assert get_default_end_time_text() == ""


def test_default_output_name_text_uses_timestamp_prefix():
    assert get_default_output_name_text().startswith("analysis_")


def test_parse_datetime_range_inputs_allows_blank_end():
    start_dt, end_dt = parse_datetime_range_inputs(
        "2026-06-07",
        "00:00",
        "",
        "",
    )

    assert start_dt.isoformat(sep=" ") == "2026-06-07 00:00:00"
    assert end_dt is None


def test_parse_datetime_range_inputs_rejects_partial_end():
    try:
        parse_datetime_range_inputs(
            "2026-06-07",
            "00:00",
            "2026-06-07",
            "",
        )
    except ValueError as exc:
        assert "結束日期與時間" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_collect_form_values_includes_time_fields():
    class FakeInput:
        def __init__(self, value: str) -> None:
            self.value = value

    class FakeCheckbox:
        def __init__(self, value: bool) -> None:
            self.value = value

    class FakeSelect:
        def __init__(self, value: str) -> None:
            self.value = value

    mapping = {
        "#path": FakeInput("./logs"),
        "#output_path": FakeInput("./exports"),
        "#output_name": FakeInput("report"),
        "#start_date": FakeInput("2026-06-07"),
        "#start_time": FakeInput("00:00"),
        "#end_date": FakeInput("2026-06-07"),
        "#end_time": FakeInput("12:00"),
        "#keyword": FakeInput("RuntimeException"),
        "#ignore_case": FakeCheckbox(True),
        "#format": FakeSelect("json"),
    }

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    values = app._collect_form_values()

    assert values == (
        "./logs",
        "./exports",
        "report",
        "2026-06-07",
        "00:00",
        "2026-06-07",
        "12:00",
        "RuntimeException",
        True,
        "json",
    )


def test_time_range_rows_have_two_inputs_each():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            start_row = app.query_one("#start-time-row")
            end_row = app.query_one("#end-time-row")

            assert len(start_row.query("Input")) == 2
            assert len(end_row.query("Input")) == 2

    asyncio.run(run_check())


def test_time_range_inputs_are_not_collapsed_in_wide_layout():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(1920, 980)) as pilot:
            await pilot.pause()

            time_stack = app.query_one(".time-stack")
            assert time_stack.region.width > 20

            for selector in ("#start_date", "#start_time", "#end_date", "#end_time"):
                field = app.query_one(selector)
                assert field.region.width > 10

    asyncio.run(run_check())


def test_form_panel_scroll_area_reaches_right_edge():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(1900, 1024)) as pilot:
            await pilot.pause()

            form_panel = app.query_one("#form-panel")
            form_right = form_panel.region.x + form_panel.region.width
            content_right = form_panel.content_region.x + form_panel.content_region.width

            assert form_right - content_right <= 1

    asyncio.run(run_check())


def test_form_controls_keep_gap_from_scrollbar():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(1292, 797)) as pilot:
            await pilot.pause()

            form_panel = app.query_one("#form-panel")
            content_right = form_panel.content_region.x + form_panel.content_region.width

            for selector in ("#clear_keyword", "#format", "#clear"):
                control = app.query_one(selector)
                control_right = control.region.x + control.region.width
                assert content_right - control_right >= 2

    asyncio.run(run_check())


def test_refresh_output_name_default_updates_only_auto_value(monkeypatch):
    class FakeInput:
        def __init__(self, value: str) -> None:
            self.value = value

    monkeypatch.setattr(
        "log_analyzer.tui.get_default_output_name_text",
        lambda: "analysis_20260607_120000",
    )

    app = LogAnalyzerApp()
    app._auto_output_name = "analysis_20260607_111111"

    auto_input = FakeInput("analysis_20260607_111111")
    app.query_one = lambda *_args, **_kwargs: auto_input  # type: ignore[assignment]

    app._refresh_output_name_default("analysis_20260607_111111")
    assert auto_input.value == "analysis_20260607_120000"
    assert app._auto_output_name == "analysis_20260607_120000"

    app._auto_output_name = "analysis_20260607_120000"
    custom_input = FakeInput("custom_report")
    app.query_one = lambda *_args, **_kwargs: custom_input  # type: ignore[assignment]
    app._refresh_output_name_default("custom_report")
    assert custom_input.value == "custom_report"
