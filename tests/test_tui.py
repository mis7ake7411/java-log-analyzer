import asyncio
from pathlib import Path
from importlib.metadata import version as package_version
from datetime import date

from log_analyzer.presentation.tui import (
    DirectoryPickerScreen,
    FilePickerScreen,
    LogAnalyzerApp,
    build_error_view,
    build_loading_view,
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


def test_file_picker_root_starts_from_system_root():
    screen = FilePickerScreen(
        initial_path=r"C:\not-used.xml",
        title="選擇 Logback XML",
        hint="",
        confirm_label="使用此檔案",
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


def test_ctrl_a_selects_focused_input_text():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            keyword = app.query_one("#keyword")
            keyword.value = "RuntimeException"
            keyword.focus()

            await pilot.press("ctrl+a")

            assert keyword.selected_text == "RuntimeException"

    asyncio.run(run_check())


def test_ctrl_u_clears_focused_input_text():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            keyword = app.query_one("#keyword")
            keyword.value = "RuntimeException"
            keyword.focus()

            await pilot.press("ctrl+u")

            assert keyword.value == ""

    asyncio.run(run_check())


def test_get_package_version_matches_installed_package():
    assert get_package_version() == package_version("java-log-analyzer")


def test_default_datetime_field_texts():
    assert get_default_start_date_text() == ""
    assert get_default_start_time_text() == ""
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


def test_parse_datetime_range_inputs_allows_start_date_only():
    start_dt, end_dt = parse_datetime_range_inputs(
        "2026-06-07",
        "",
        "",
        "",
    )

    assert start_dt is not None
    assert start_dt.isoformat(sep=" ") == "2026-06-07 00:00:00"
    assert end_dt is None


def test_parse_datetime_range_inputs_allows_end_date_only():
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "2026-06-07",
        "",
    )

    assert start_dt is None
    assert end_dt is not None
    assert end_dt.isoformat(sep=" ") == "2026-06-07 23:59:59"


def test_parse_datetime_range_inputs_allows_blank_start_and_end():
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "",
        "",
    )

    assert start_dt is None
    assert end_dt is None


def test_parse_datetime_range_inputs_allows_end_only():
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "2026-06-07",
        "12:00",
    )

    assert start_dt is None
    assert end_dt is not None
    assert end_dt.isoformat(sep=" ") == "2026-06-07 12:00:00"


def test_parse_datetime_range_inputs_accepts_compact_numeric_values():
    start_dt, end_dt = parse_datetime_range_inputs(
        "20260607",
        "1820",
        "20260608",
        "235959",
    )

    assert start_dt is not None
    assert end_dt is not None
    assert start_dt.isoformat(sep=" ") == "2026-06-07 18:20:00"
    assert end_dt.isoformat(sep=" ") == "2026-06-08 23:59:59"


def test_parse_datetime_range_inputs_accepts_compact_numeric_date_only():
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "20260607",
        "",
    )

    assert start_dt is None
    assert end_dt is not None
    assert end_dt.isoformat(sep=" ") == "2026-06-07 23:59:59"


def test_parse_datetime_range_inputs_rejects_start_time_without_date():
    try:
        parse_datetime_range_inputs(
            "",
            "00:00",
            "",
            "",
        )
    except ValueError as exc:
        assert "開始日期" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_parse_datetime_range_inputs_rejects_end_time_without_date():
    try:
        parse_datetime_range_inputs(
            "",
            "",
            "",
            "12:00",
        )
    except ValueError as exc:
        assert "結束日期" in str(exc)
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
        "#pattern_mode": FakeSelect("custom"),
        "#log_pattern": FakeInput("%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} - %msg%n"),
        "#sort_by": FakeSelect("level"),
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
        "custom",
        "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} - %msg%n",
        "level",
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


def test_log_pattern_controls_exist():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.query_one("#pattern_mode")
            assert app.query_one("#log_pattern")

    asyncio.run(run_check())


def test_sort_control_exists():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.query_one("#sort_by")

    asyncio.run(run_check())


def test_logback_xml_controls_exist():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.query_one("#logback_xml_path")
            assert app.query_one("#browse_logback_xml")

    asyncio.run(run_check())


def test_load_logback_xml_action_fills_pattern(monkeypatch, tmp_path):
    xml_file = tmp_path / "logback.xml"
    xml_file.write_text("<configuration />", encoding="utf-8")

    class FakeInput:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    class FakeSelect:
        def __init__(self, value: str) -> None:
            self.value = value

    class FakeStatic:
        def __init__(self) -> None:
            self.value = ""

        def update(self, value: str) -> None:
            self.value = value

    class FakePattern:
        name = "FILE Pattern"
        pattern = "%d %-5level [%thread] %logger{0}: %msg%n"
        matches = 2
        checked = 2

    mapping = {
        "#logback-xml-status": FakeStatic(),
        "#logback_xml_path": FakeInput(str(xml_file)),
        "#path": FakeInput("./logs"),
        "#pattern_mode": FakeSelect("default"),
        "#log_pattern": FakeInput(),
    }

    monkeypatch.setattr("log_analyzer.presentation.tui.find_best_logback_pattern", lambda *_args: FakePattern())

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    app.action_load_logback_xml()

    assert mapping["#pattern_mode"].value == "custom"
    assert mapping["#log_pattern"].value == "%d %-5level [%thread] %logger{0}: %msg%n"
    assert "命中 2/2" in mapping["#logback-xml-status"].value


def test_apply_selected_logback_xml_updates_input():
    class FakeInput:
        def __init__(self) -> None:
            self.value = ""
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    app = LogAnalyzerApp()
    fake_input = FakeInput()
    app.query_one = lambda *_args, **_kwargs: fake_input  # type: ignore[assignment]

    app._apply_selected_logback_xml(r"C:\logs\logback-spring.xml")

    assert fake_input.value == r"C:\logs\logback-spring.xml"
    assert fake_input.focused is True


def test_apply_and_load_logback_xml_updates_pattern(monkeypatch, tmp_path):
    xml_file = tmp_path / "logback.xml"
    xml_file.write_text("<configuration />", encoding="utf-8")

    class FakeInput:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    class FakeSelect:
        def __init__(self, value: str) -> None:
            self.value = value

    class FakeStatic:
        def __init__(self) -> None:
            self.value = ""

        def update(self, value: str) -> None:
            self.value = value

    class FakePattern:
        name = "FILE Pattern"
        pattern = "%d %-5level [%thread] %logger{0}: %msg%n"
        matches = 2
        checked = 2

    mapping = {
        "#logback-xml-status": FakeStatic(),
        "#logback_xml_path": FakeInput(),
        "#path": FakeInput("./logs"),
        "#pattern_mode": FakeSelect("default"),
        "#log_pattern": FakeInput(),
    }

    monkeypatch.setattr("log_analyzer.presentation.tui.find_best_logback_pattern", lambda *_args: FakePattern())

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    app._apply_and_load_logback_xml(str(xml_file))

    assert mapping["#logback_xml_path"].value == str(xml_file)
    assert mapping["#pattern_mode"].value == "custom"
    assert mapping["#log_pattern"].value == "%d %-5level [%thread] %logger{0}: %msg%n"
    assert "命中 2/2" in mapping["#logback-xml-status"].value


def test_loading_view_explains_processing_state():
    panel = build_loading_view()
    lines = [str(renderable) for renderable in panel.renderable.renderables]

    assert panel.title == "執行中"
    assert "分析中，請稍候..." in lines[0]
    assert "正在掃描 Log 目錄" in lines[1]
    assert "大型資料夾" in lines[2]


def test_error_view_includes_next_step_hint():
    panel = build_error_view("找不到資料夾", "路徑不存在：/tmp/missing")
    lines = [str(renderable) for renderable in panel.renderable.renderables]

    assert panel.title == "執行失敗"
    assert "找不到資料夾" in lines[0]
    assert "路徑不存在" in lines[1]
    assert "重新選擇 Log 目錄" in lines[2]


def test_apply_selected_directory_autofills_logback_settings(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Hello\n",
        encoding="utf-8",
    )
    xml_file = log_dir / "logback-spring.xml"
    xml_file.write_text("<configuration />", encoding="utf-8")

    class FakeInput:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    class FakeSelect:
        def __init__(self, value: str) -> None:
            self.value = value

    class FakeStatic:
        def __init__(self) -> None:
            self.value = ""

        def update(self, value) -> None:
            self.value = value

    class FakePattern:
        name = "FILE Pattern"
        pattern = "%d %-5level [%thread] %logger{36} - %msg%n"
        matches = 2
        checked = 2

        @property
        def score(self) -> float:
            return self.matches / self.checked

    mapping = {
        "#path": FakeInput(str(log_dir)),
        "#path-status": FakeStatic(),
        "#logback_xml_path": FakeInput(),
        "#log_pattern": FakeInput(),
        "#pattern_mode": FakeSelect("default"),
        "#logback-xml-status": FakeStatic(),
    }

    monkeypatch.setattr("log_analyzer.presentation.tui.find_best_logback_pattern", lambda *_args: FakePattern())

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    app._apply_selected_directory("path", str(log_dir))

    assert mapping["#logback_xml_path"].value == str(xml_file)
    assert mapping["#log_pattern"].value == "%d %-5level [%thread] %logger{36} - %msg%n"
    assert mapping["#pattern_mode"].value == "custom"
    assert "已自動載入" in mapping["#logback-xml-status"].value
    assert xml_file.name in mapping["#logback-xml-status"].value


def test_apply_selected_directory_does_not_override_manual_logback_values(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Hello\n",
        encoding="utf-8",
    )
    (log_dir / "logback-spring.xml").write_text("<configuration />", encoding="utf-8")

    class FakeInput:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    class FakeSelect:
        def __init__(self, value: str) -> None:
            self.value = value

    class FakeStatic:
        def __init__(self) -> None:
            self.value = ""

        def update(self, value) -> None:
            self.value = value

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("手動值不應觸發自動掃描")

    mapping = {
        "#path": FakeInput(str(log_dir)),
        "#path-status": FakeStatic(),
        "#logback_xml_path": FakeInput("/manual/logback.xml"),
        "#log_pattern": FakeInput("manual pattern"),
        "#pattern_mode": FakeSelect("default"),
        "#logback-xml-status": FakeStatic(),
    }

    monkeypatch.setattr("log_analyzer.presentation.tui.find_best_logback_pattern", fail_if_called)

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    app._apply_selected_directory("path", str(log_dir))

    assert mapping["#logback_xml_path"].value == "/manual/logback.xml"
    assert mapping["#log_pattern"].value == "manual pattern"
    assert mapping["#pattern_mode"].value == "default"
    assert mapping["#logback-xml-status"].value == ""


def test_time_range_inputs_are_not_collapsed_in_wide_layout():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(1920, 980)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            await pilot.pause()

            time_section = app.query_one(".time-section")
            assert time_section.region.width > 20

            for selector in ("#start-date-group", "#start-time-group", "#end-date-group", "#end-time-group"):
                group = app.query_one(selector)
                assert group.region.width > 10

            for selector in ("#start_date", "#start_time", "#end_date", "#end_time"):
                field = app.query_one(selector)
                assert field.region.width > 10

    asyncio.run(run_check())


def test_tab_order_follows_vertical_form_flow_when_advanced_settings_are_hidden():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(1400, 980)) as pilot:
            await pilot.pause()

            sequence = []
            for _ in range(9):
                focused = app.focused
                sequence.append(focused.id if focused and getattr(focused, "id", None) else None)
                await pilot.press("tab")
                await pilot.pause()

            assert sequence == [
                "path",
                "browse_path",
                "output_path",
                "browse_output_path",
                "keyword",
                "clear_keyword",
                "toggle_advanced_settings",
                "run",
                "clear",
            ]

    asyncio.run(run_check())


def test_tab_order_follows_vertical_form_flow_when_advanced_settings_are_visible():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(1400, 980)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            await pilot.pause()

            sequence = []
            for _ in range(19):
                focused = app.focused
                sequence.append(focused.id if focused and getattr(focused, "id", None) else None)
                await pilot.press("tab")
                await pilot.pause()

            assert sequence == [
                "path",
                "browse_path",
                "output_path",
                "browse_output_path",
                "keyword",
                "clear_keyword",
                "toggle_advanced_settings",
                "output_name",
                "logback_xml_path",
                "browse_logback_xml",
                "pattern_mode",
                "log_pattern",
                "start_date",
                "start_time",
                "end_date",
                "end_time",
                "ignore_case",
                "sort_by",
                "format",
            ]

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
        "log_analyzer.presentation.tui.get_default_output_name_text",
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


def test_advanced_settings_toggle_changes_visibility_and_label():
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test():
            advanced = app.query_one("#advanced-settings")
            toggle = app.query_one("#toggle_advanced_settings")

            assert advanced.display is False
            assert str(toggle.label) == "展開進階設定"

            app.action_toggle_advanced_settings()
            assert advanced.display is True
            assert str(toggle.label) == "收合進階設定"

            app.action_toggle_advanced_settings()
            assert advanced.display is False
            assert str(toggle.label) == "展開進階設定"

    asyncio.run(run_check())
