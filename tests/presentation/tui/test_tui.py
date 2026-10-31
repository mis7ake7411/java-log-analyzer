from log_analyzer.presentation.tui.controllers import patterns as patterns_module
from log_analyzer.presentation.tui.controllers import results as results_module
from tui_fixtures import make_analysis_result
from dataclasses import replace
import asyncio
from datetime import datetime
import logging
import pickle
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from rich.console import Console
from textual.errors import NoWidget
from textual.events import Paste

from tui_waits import (
    press_tab_and_wait_for_focus as _press_tab_and_wait_for_focus,
    set_path_input_and_wait,
    wait_for_ui_state as _wait_for_ui_state,
    wait_for_scheduled_analysis as _wait_for_scheduled_analysis,
    wait_for_layout_to_settle as _wait_for_layout_to_settle,
)

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.infrastructure.paths import get_system_root_path
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.components.time_range import TimeRangeSettings
from log_analyzer.presentation.tui.screens.path_picker import DirectoryPickerScreen, FilePickerScreen
from log_analyzer.presentation.tui.form_values import get_default_end_date_text, get_default_end_time_text, get_default_output_name_text, get_default_start_date_text, get_default_start_time_text, parse_datetime_range_inputs
from log_analyzer.presentation.tui.views import (
    build_dashboard_view,
    build_error_view,
    build_loading_view,
)
from log_analyzer.domain.parser import MatchedLogsStore
from log_analyzer.presentation.tui.screens.event_browser import EventBrowserScreen
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.error_messages import get_error_hint
from log_analyzer.presentation.tui import views as tui_views
from log_analyzer.presentation.tui.recent_state import (
    load_recent_tui_state,
    save_recent_tui_state,
)
from log_analyzer.presentation.tui.views import analyze_dashboard
from tui_harnesses import AdvancedSettingsHarness, TimeRangeSettingsHarness
from log_analyzer.version import get_package_version


# run_test 的尺寸單位是字元欄／列；240 欄同時保留主畫面與進階區塊的寬版配置。
# 一般寬版保留足夠高度；捲動案例另用 40 列，確保仍必須捲動表單。
WIDE_TERMINAL_SIZE = (240, 80)
WIDE_SCROLL_TERMINAL_SIZE = (240, 40)


class _FakeSourceScopePreview:
    def show_source(self, _source: object) -> None:
        pass

    def show_invalid(self, _message: str) -> None:
        pass


def test_tui_loads_structured_responsive_stylesheets():
    """情境：TUI 應載入拆分的響應式樣式表。"""
    css_paths = [Path(path).name for path in LogAnalyzerApp.CSS_PATH]

    assert css_paths == [
        "tui.base.tcss",
        "tui.layout.tcss",
        "tui.dialog.tcss",
        "tui.responsive.layout.tcss",
        "tui.responsive.form.tcss",
        "tui.responsive.advanced.tcss",
        "tui.time-range.tcss",
        "tui.live.tcss",
    ]
    assert not (Path(__file__).parents[3] / "src/log_analyzer/tcss/tui.responsive.tcss").exists()


def test_handle_unexpected_error_logs_and_displays_error(caplog):
    """情境：非預期錯誤應記錄堆疊並顯示訊息。"""
    status_states = []
    app = SimpleNamespace(
        _last_result=None,
        _set_result_status=status_states.append,
        is_running=True,
    )
    result_box = object()
    displayed = {}
    app._set_result = lambda box, view: displayed.update(box=box, view=view)

    with caplog.at_level(logging.ERROR):
        LogAnalyzerApp._handle_unexpected_error(app, result_box, RuntimeError("unexpected failure"))

    assert app._last_result is None
    assert displayed["box"] is result_box
    assert status_states == ["failed"]
    assert "unexpected failure" in caplog.text


def test_get_system_root_path_returns_current_drive_root():
    """情境：系統根路徑應對應目前磁碟根目錄。"""
    expected_root = Path(Path.cwd().anchor or "/")

    assert get_system_root_path() == expected_root


def test_directory_picker_root_starts_from_system_root():
    """情境：資料夾選擇器應從系統根目錄開始。"""
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
    """情境：檔案選擇器應從系統根目錄開始。"""
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
    """情境：清除關鍵字操作應清空欄位。"""
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
    """情境：Ctrl+A 應選取焦點輸入框的全部文字。"""
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
    """情境：Ctrl+U 應清空焦點輸入框。"""
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


def test_get_package_version_matches_project_version():
    """情境：畫面版本應與專案版號一致。"""
    project_toml = Path(__file__).parents[3] / "pyproject.toml"
    version_match = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"\s*$',
        project_toml.read_text(encoding="utf-8"),
    )

    assert version_match is not None
    assert get_package_version() == version_match.group(1)


def test_default_datetime_field_texts():
    """情境：日期時間欄位應有預期的預設文字。"""
    assert get_default_start_date_text() == ""
    assert get_default_start_time_text() == ""
    assert get_default_end_date_text() == ""
    assert get_default_end_time_text() == ""


def test_default_output_name_text_uses_timestamp_prefix():
    """情境：預設輸出名稱應帶時間戳前綴。"""
    assert get_default_output_name_text().startswith("analysis_")


def test_composed_ui_preserves_key_widget_contracts(tmp_path, monkeypatch):
    """情境：組裝畫面應保留關鍵元件契約。"""
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.query_one("#hero-title").render() == "Java Log Analyzer"
            assert app.query_one("#path").value == "."
            assert app.query_one("#output_path").value == "."
            assert app.query_one("#keyword")
            assert app.query_one("#advanced-settings")
            advanced = app.query_one("#advanced-settings")
            assert advanced.query_one("#sort_by")
            assert advanced.query_one("#logback_xml_path")
            assert advanced.query_one("#browse_logback_xml")
            time_range = advanced.query_one(TimeRangeSettings)
            assert time_range.query_one("#time-preset")
            assert time_range.query_one("#start-time-row")
            assert time_range.query_one("#end-time-row")
            assert app.query_one("#manage-recipes")
            assert app.query_one("#output_name").value.startswith("analysis_")
            assert app.query_one("#ignore_case").value is True
            assert app.query_one("#format").value == "csv"
            assert app.query_one("#level_ERROR").value is True
            assert app.query_one("#level_WARN").value is True
            assert app.query_one("#level_INFO").value is True
            assert app.query_one("#level_DEBUG").value is False
            assert app.query_one("#level_TRACE").value is False
            assert app.query_one("#run")
            assert app.query_one("#clear")
            assert app.query_one("#result-box")

    asyncio.run(run_check())


def test_parse_datetime_range_inputs_allows_blank_end():
    """情境：時間區間應允許留空終點。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "2026-06-07",
        "00:00",
        "",
        "",
    )

    assert start_dt.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 00:00:00"
    assert end_dt is None


def test_parse_datetime_range_inputs_allows_start_date_only():
    """情境：時間區間應允許只填開始日期。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "2026-06-07",
        "",
        "",
        "",
    )

    assert start_dt is not None
    assert start_dt.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 00:00:00"
    assert end_dt is None


def test_parse_datetime_range_inputs_allows_end_date_only():
    """情境：時間區間應允許只填結束日期。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "2026-06-07",
        "",
    )

    assert start_dt is None
    assert end_dt is not None
    assert end_dt.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 23:59:59"


def test_parse_datetime_range_inputs_allows_blank_start_and_end():
    """情境：時間起訖皆空白時應表示不限制。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "",
        "",
    )

    assert start_dt is None
    assert end_dt is None


def test_parse_datetime_range_inputs_allows_end_only():
    """情境：只填終點時應可建立有效時間區間。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "2026-06-07",
        "12:00",
    )

    assert start_dt is None
    assert end_dt is not None
    assert end_dt.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 12:00:00"


def test_parse_datetime_range_inputs_accepts_compact_numeric_values():
    """情境：日期時間欄位應接受緊湊數字格式。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "20260607",
        "1820",
        "20260608",
        "235959",
    )

    assert start_dt is not None
    assert end_dt is not None
    assert start_dt.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 18:20:00"
    assert end_dt.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-08 23:59:59"


def test_parse_datetime_range_inputs_accepts_compact_numeric_date_only():
    """情境：只填緊湊數字日期時應可解析。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "",
        "",
        "20260607",
        "",
    )

    assert start_dt is None
    assert end_dt is not None
    assert end_dt.replace(tzinfo=None).isoformat(sep=" ") == "2026-06-07 23:59:59"


def test_parse_datetime_range_inputs_assigns_system_local_timezone():
    """情境：時間區間應補上系統本地時區。"""
    start_dt, end_dt = parse_datetime_range_inputs(
        "2026-06-07",
        "10:00",
        "2026-06-07",
        "12:00",
    )

    assert start_dt.tzinfo == datetime.now().astimezone().tzinfo
    assert end_dt.tzinfo == datetime.now().astimezone().tzinfo


def test_parse_datetime_range_inputs_rejects_start_time_without_date():
    """情境：只有開始時間沒有日期時應拒絕。"""
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
    """情境：只有結束時間沒有日期時應拒絕。"""
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
    """情境：收集表單應包含日期與時間欄位。"""
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
        "#start_date": FakeInput("2026-06-07"),
        "#start_time": FakeInput("00:00"),
        "#end_date": FakeInput("2026-06-07"),
        "#end_time": FakeInput("12:00"),
        "#keyword": FakeInput("RuntimeException"),
        "#manual_pattern_override": FakeCheckbox(True),
        "#log_pattern": FakeInput("%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} - %msg%n"),
        "#sort_by": FakeSelect("level"),
        "#level_ERROR": FakeCheckbox(True),
        "#level_WARN": FakeCheckbox(True),
        "#level_INFO": FakeCheckbox(False),
        "#level_DEBUG": FakeCheckbox(True),
        "#level_TRACE": FakeCheckbox(False),
        "#level_unrestricted": FakeCheckbox(False),
        "#ignore_case": FakeCheckbox(True),
    }

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    request, legacy_display_mode = app._collect_form_values()
    local_timezone = datetime.now().astimezone().tzinfo

    assert request == AnalysisRequest(
        path="./logs",
        start_dt=datetime(2026, 6, 7, 0, 0, tzinfo=local_timezone),
        end_dt=datetime(2026, 6, 7, 12, 0, tzinfo=local_timezone),
        keyword="RuntimeException",
        ignore_case=True,
        sort_by="level",
        log_pattern="%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} - %msg%n",
        levels=("ERROR", "WARN", "DEBUG"),
    )
    assert legacy_display_mode == "full"


def test_display_settings_rows_follow_current_field_grouping():
    """情境：設定列應符合目前欄位分組。"""
    async def run_check() -> None:
        app = AdvancedSettingsHarness()
        async with app.run_test() as pilot:
            advanced = app.query_one("#advanced-settings")
            await _wait_for_layout_to_settle(pilot, (advanced,))
            field_rows = [child for child in advanced.children if child.has_class("field-row")]

            assert len(field_rows) == 2

            first_row = field_rows[0]
            second_row = field_rows[1]

            first_row_classes = [
                frozenset(child.classes)
                for child in first_row.children
                if child.has_class("field")
            ]
            second_row_classes = [
                frozenset(child.classes)
                for child in second_row.children
                if child.has_class("field")
            ]

            assert frozenset({"field", "field--inline", "field--ignore"}) in first_row_classes
            assert second_row_classes == [frozenset({"field", "field--inline", "field--sort"})]

            export_settings = app.query_one("#export-settings")
            export_row = next(child for child in export_settings.children if child.has_class("field-row"))
            export_row_classes = [
                frozenset(child.classes)
                for child in export_row.children
                if child.has_class("field")
            ]
            assert frozenset({"field", "field--inline", "field--format"}) in export_row_classes
            assert frozenset({"field", "field--inline", "field--split-output"}) in export_row_classes
            assert app.query_one("#format").parent.has_class("field--format")

    asyncio.run(run_check())


def test_recent_tui_state_round_trip_only_keeps_selected_fields(tmp_path, monkeypatch):
    """情境：最近設定往返只應保存允許欄位。"""
    monkeypatch.chdir(tmp_path)

    save_recent_tui_state(
        {
            "path": "./logs",
            "output_path": "./exports",
            "keyword": "RuntimeException",
            "ignore_case": True,
            "display_mode": "summary",
            "sort_by": "level",
            "levels": ["ERROR", "WARN"],
            "split_by_level": True,
            "format": "json",
            "start_date": "2026-06-07",
            "start_time": "00:00",
            "end_date": "2026-06-07",
            "end_time": "12:00",
            "output_name": "should-not-be-saved",
            "log_pattern": "should-not-be-saved",
            "pattern_mode": "custom",
        }
    )

    state = load_recent_tui_state()

    assert state == {
        "path": "./logs",
        "output_path": "./exports",
        "keyword": "RuntimeException",
        "ignore_case": True,
        "display_mode": "summary",
        "sort_by": "level",
        "levels": ["ERROR", "WARN"],
        "split_by_level": True,
        "format": "json",
        "start_date": "2026-06-07",
        "start_time": "00:00",
        "end_date": "2026-06-07",
        "end_time": "12:00",
    }


def test_recent_tui_state_missing_file_returns_empty_state(tmp_path, monkeypatch):
    """情境：最近設定檔不存在時應回空設定。"""
    monkeypatch.chdir(tmp_path)

    state = load_recent_tui_state()

    assert state == {}


def test_tui_restores_recent_state_on_mount(tmp_path, monkeypatch):
    """情境：掛載 TUI 時應還原最近設定。"""
    monkeypatch.chdir(tmp_path)
    save_recent_tui_state(
        {
            "path": "./logs",
            "output_path": "./exports",
            "keyword": "RuntimeException",
            "ignore_case": True,
            "display_mode": "full",
            "sort_by": "level",
            "levels": ["ERROR", "DEBUG"],
            "split_by_level": True,
            "format": "md",
            "start_date": "2026-06-07",
            "start_time": "00:00",
            "end_date": "2026-06-08",
            "end_time": "12:00",
        }
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.query_one("#path").value == "./logs"
            assert app.query_one("#output_path").value == "./exports"
            assert app.query_one("#keyword").value == "RuntimeException"
            assert app.query_one("#ignore_case").value is True
            assert len(app.query("#display_mode")) == 0
            assert app.query_one("#sort_by").value == "level"
            assert app.query_one("#level_ERROR").value is True
            assert app.query_one("#level_WARN").value is False
            assert app.query_one("#level_INFO").value is False
            assert app.query_one("#level_DEBUG").value is True
            assert app.query_one("#level_TRACE").value is False
            assert app.query_one("#split_by_level").value is True
            assert app.query_one("#format").value == "md"
            assert app.query_one("#start_date").value == "2026-06-07"
            assert app.query_one("#start_time").value == "00:00"
            assert app.query_one("#end_date").value == "2026-06-08"
            assert app.query_one("#end_time").value == "12:00"
            assert app.query_one("#output_name").value != ""
            assert app.query_one("#output_name").value != "should-not-be-saved"

    asyncio.run(run_check())


def test_tui_does_not_focus_input_on_startup():
    """情境：啟動時不應自動將焦點放到輸入框。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            assert app.focused is None

    asyncio.run(run_check())


def test_tui_shortcut_works_without_input_focus():
    """情境：沒有輸入焦點時快捷鍵仍應可用。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            await pilot.press("a")
            await pilot.pause()

            assert app.query_one("#advanced-settings").display is True

    asyncio.run(run_check())


def test_time_range_rows_have_two_inputs_each():
    """情境：起訖時間列都應包含日期與時間兩個輸入。"""
    async def run_check() -> None:
        app = TimeRangeSettingsHarness()
        async with app.run_test() as pilot:
            start_row = app.query_one("#start-time-row")
            end_row = app.query_one("#end-time-row")
            await _wait_for_layout_to_settle(pilot, (start_row, end_row))

            assert len(start_row.query("Input")) == 2
            assert len(end_row.query("Input")) == 2

    asyncio.run(run_check())


def test_log_pattern_controls_show_auto_source_and_manual_override():
    """情境：Pattern 區應提供自動來源與手動覆寫。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert len(list(app.query("#pattern_mode"))) == 0
            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#log_pattern")
            assert "預設 Logback" in app.query_one("#pattern-source").render().plain
            assert "%d{yyyy-MM-dd HH:mm:ss.SSS}" in app.query_one("#pattern-preview").render().plain

    asyncio.run(run_check())


def test_manual_pattern_field_is_hidden_until_override_is_enabled():
    """情境：未啟用覆寫時應隱藏手動 Pattern 輸入。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 80)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()

            advanced = app.query_one("#advanced-settings")
            manual_override = app.query_one("#manual_pattern_override")
            pattern_field = app.query_one("#pattern-field")

            def manual_override_can_be_clicked() -> bool:
                region = manual_override.region
                if not (
                    advanced.display
                    and advanced.content_size.width > 0
                    and (
                        advanced.has_class("advanced-wide")
                        or advanced.has_class("advanced-compact")
                    )
                    and manual_override.display
                    and not manual_override.disabled
                    and region.width > 0
                    and region.height > 0
                ):
                    return False
                x = region.x + region.width // 2
                y = region.y + region.height // 2
                if not app.screen.region.contains(x, y):
                    return False
                try:
                    hit, _ = app.screen.get_widget_at(x, y)
                except NoWidget:
                    return False
                return hit is manual_override

            await _wait_for_layout_to_settle(
                pilot,
                (advanced, manual_override),
                manual_override_can_be_clicked,
            )
            assert pattern_field.display is False

            clicked = await pilot.click(
                "#manual_pattern_override",
                offset=(manual_override.region.width // 2, manual_override.region.height // 2),
            )
            assert clicked, f"manual override click did not hit its checkbox: {manual_override.region}"
            await _wait_for_ui_state(
                pilot,
                lambda: pattern_field.display,
                "manual override did not reveal the Pattern field",
            )

            assert pattern_field.display is True

    asyncio.run(run_check())


def test_advanced_settings_title_stays_at_top_of_panel():
    """情境：進階設定標題應留在面板頂端。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()

            advanced_title = app.query_one("#advanced-settings .section-title--sub")
            advanced_settings = app.query_one("#advanced-settings")
            advanced_title_row = app.query_one("#advanced-settings-title-row")
            await _wait_for_layout_to_settle(
                pilot,
                (advanced_settings, advanced_title_row, advanced_title),
                lambda: advanced_settings.display
                and advanced_title_row.region.width > 0
                and advanced_title.region.width > 0,
            )

            assert advanced_title_row.region.height <= 2
            assert advanced_title.region.y <= advanced_settings.region.y + 3
            assert advanced_title.region.width == advanced_title_row.region.width

    asyncio.run(run_check())


def test_advanced_settings_logback_browse_button_keeps_right_edge_aligned():
    """情境：寬版進階設定的 XML 瀏覽按鈕右緣應對齊。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()

            logback_xml_path = app.query_one("#logback_xml_path")
            browse_button = app.query_one("#browse_logback_xml")
            path_row = logback_xml_path.parent
            await _wait_for_ui_state(
                pilot,
                lambda: (
                    logback_xml_path.region.width > 0
                    and browse_button.region.width > 0
                    and browse_button.region.height > 0
                ),
                "Logback browse control did not enter the layout",
            )
            await _wait_for_layout_to_settle(
                pilot,
                (logback_xml_path, browse_button, path_row, app.query_one("#advanced-settings")),
            )

            assert app.query_one("#advanced-settings").has_class("advanced-wide")
            assert browse_button.region.width <= 10
            assert browse_button.region.x + browse_button.region.width <= path_row.region.x + path_row.region.width
            assert logback_xml_path.region.x + logback_xml_path.region.width < browse_button.region.x + browse_button.region.width

    asyncio.run(run_check())


def test_advanced_toggle_aligns_with_action_buttons():
    """情境：進階設定切換應與操作按鈕對齊。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()

            toggle = app.query_one("#toggle_advanced_settings")
            run_button = app.query_one("#run")

            assert toggle.region.x + toggle.region.width == run_button.region.x + run_button.region.width

    asyncio.run(run_check())


def test_sort_control_exists():
    """情境：表單應包含排序控制。"""
    async def run_check() -> None:
        app = AdvancedSettingsHarness()
        async with app.run_test() as pilot:
            sort_by = app.query_one("#sort_by")
            await _wait_for_layout_to_settle(pilot, (sort_by,))

            assert app.query_one("#sort_by")

    asyncio.run(run_check())


def test_level_filter_defaults_to_common_levels():
    """情境：預設 Level 應為常用層級。"""
    async def run_check() -> None:
        app = AdvancedSettingsHarness()
        async with app.run_test() as pilot:
            levels = tuple(
                app.query_one(f"#level_{level}")
                for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")
            )
            await _wait_for_layout_to_settle(pilot, levels)

            assert app.query_one("#level_ERROR").value is True
            assert app.query_one("#level_WARN").value is True
            assert app.query_one("#level_INFO").value is True
            assert app.query_one("#level_DEBUG").value is False
            assert app.query_one("#level_TRACE").value is False

    asyncio.run(run_check())


def test_level_filter_select_all_and_clear_actions():
    """情境：Level 全選與清除應更新選項。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            app._set_all_levels(False)
            assert app.query_one("#level_ERROR").value is False
            assert app.query_one("#level_TRACE").value is False

            app._set_all_levels(True)
            assert app.query_one("#level_ERROR").value is True
            assert app.query_one("#level_TRACE").value is True

    asyncio.run(run_check())


def test_level_filter_controls_use_full_field_body_width_in_wide_layout():
    """情境：寬版 Level 控制應使用欄位本文可用寬度。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(184, 90)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()

            level_row = app.query_one(".level-filter-row")
            advanced = app.query_one("#advanced-settings")
            actions = app.query_one(".level-filter-actions")
            error_checkbox = app.query_one("#level_ERROR")
            info_checkbox = app.query_one("#level_INFO")
            debug_checkbox = app.query_one("#level_DEBUG")
            trace_checkbox = app.query_one("#level_TRACE")
            select_all = app.query_one("#select_all_levels")
            clear_levels = app.query_one("#clear_levels")
            await _wait_for_layout_to_settle(
                pilot,
                (level_row, actions, error_checkbox, info_checkbox, debug_checkbox, trace_checkbox, select_all, clear_levels),
                lambda: advanced.display
                and advanced.has_class("advanced-wide")
                and all(
                    widget.region.width > 0 and widget.region.height > 0
                    for widget in (
                        level_row,
                        actions,
                        error_checkbox,
                        info_checkbox,
                        debug_checkbox,
                        trace_checkbox,
                        select_all,
                        clear_levels,
                    )
                ),
            )

            assert level_row.region.width >= 40
            assert actions.region.width == level_row.region.width
            assert error_checkbox.region.width > 5
            assert info_checkbox.region.y == error_checkbox.region.y
            assert debug_checkbox.region.y > error_checkbox.region.y
            assert trace_checkbox.region.x + trace_checkbox.region.width <= level_row.region.x + level_row.region.width
            assert select_all.region.x + select_all.region.width < clear_levels.region.x

    asyncio.run(run_check())


def test_level_filter_controls_reflow_in_compact_layout():
    """情境：窄版 Level 控制應重新分配版面。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(70, 40)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()

            form_panel = app.query_one("#form-panel")
            advanced = app.query_one("#advanced-settings")
            level_row = app.query_one(".level-filter-row")
            error_checkbox = app.query_one("#level_ERROR")
            info_checkbox = app.query_one("#level_INFO")
            select_all = app.query_one("#select_all_levels")
            clear_levels = app.query_one("#clear_levels")
            await _wait_for_layout_to_settle(
                pilot,
                (
                    form_panel,
                    advanced,
                    level_row,
                    error_checkbox,
                    info_checkbox,
                    select_all,
                    clear_levels,
                ),
                lambda: advanced.display
                and advanced.content_size.width > 0
                and advanced.has_class("advanced-compact")
                and all(
                    widget.region.width > 0 and widget.region.height > 0
                    for widget in (
                        level_row,
                        error_checkbox,
                        info_checkbox,
                        select_all,
                        clear_levels,
                    )
                ),
            )

            assert form_panel.has_class("compact")
            assert info_checkbox.region.y > error_checkbox.region.y
            assert error_checkbox.region.x + error_checkbox.region.width <= level_row.region.x + level_row.region.width
            assert clear_levels.region.y > select_all.region.y

    asyncio.run(run_check())


def test_logback_xml_controls_exist():
    """情境：表單應提供 Logback XML 控制元件。"""
    async def run_check() -> None:
        app = AdvancedSettingsHarness()
        async with app.run_test() as pilot:
            controls = (
                app.query_one("#logback_xml_path"),
                app.query_one("#browse_logback_xml"),
            )
            await _wait_for_layout_to_settle(pilot, controls)

            assert app.query_one("#logback_xml_path")
            assert app.query_one("#browse_logback_xml")

    asyncio.run(run_check())


def test_load_logback_xml_action_updates_preview_without_enabling_override(monkeypatch, tmp_path):
    """情境：載入 XML 應更新預覽但不啟用手動覆寫。"""
    xml_file = tmp_path / "logback.xml"
    xml_file.write_text(
        '<configuration><property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/></configuration>',
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: Starting YtMamApp\n"
        "2026-05-25 15:50:34,198 WARN  [main] YtMamApp: Starting YtMamApp\n",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#logback_xml_path").value = str(xml_file)
            app.query_one("#path").value = str(log_dir)
            app.query_one("#log_pattern").value = "keep this manual draft"

            app.action_load_logback_xml()

            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#log_pattern").value == "keep this manual draft"
            assert "%d %-5level [%thread] %logger{0}: %msg%n" in app.query_one("#pattern-preview").render().plain
            assert xml_file.name in app.query_one("#pattern-source").render().plain
            assert "命中 2/2" in app.query_one("#logback-xml-status").render().plain

    asyncio.run(run_check())


def test_apply_selected_logback_xml_updates_input():
    """情境：選取 XML 應更新來源欄位。"""
    class FakeInput:
        def __init__(self) -> None:
            self.value = ""
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    class FakeCheckbox:
        value = False

    class FakeStatic:
        def __init__(self) -> None:
            self.value = ""

        def update(self, value: str) -> None:
            self.value = value

    app = LogAnalyzerApp()
    fake_input = FakeInput()
    widgets = {
        "#logback_xml_path": fake_input,
        "#manual_pattern_override": FakeCheckbox(),
        "#pattern-source": FakeStatic(),
        "#pattern-preview": FakeStatic(),
    }
    app.query_one = lambda selector, *_args, **_kwargs: widgets[selector]  # type: ignore[assignment]

    app._apply_selected_logback_xml(r"C:\logs\logback-spring.xml")

    assert fake_input.value == r"C:\logs\logback-spring.xml"
    assert fake_input.focused is True


def test_paste_invalid_path_keeps_focused_input_value(tmp_path):
    """情境：貼上無效路徑應保留焦點欄位的文字。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            path_input = app.query_one("#path")
            await set_path_input_and_wait(pilot, path_input, str(tmp_path))
            path_input.focus()
            await pilot.pause()

            invalid_path = tmp_path / "not-a-real-log-directory"
            assert not invalid_path.exists()
            path_input.post_message(Paste(str(invalid_path)))
            await _wait_for_ui_state(
                pilot,
                lambda: "路徑不存在" in str(app.query_one("#path-status").render()),
                "invalid source path status did not refresh",
            )

            assert path_input.value == str(tmp_path)
            assert "路徑不存在" in str(app.query_one("#path-status").render())

    asyncio.run(run_check())


def test_paste_invalid_output_path_keeps_value_and_displays_error(tmp_path):
    """情境：貼上無效輸出路徑應保留文字並顯示錯誤。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            result = make_analysis_result(tmp_path)
            app._last_result = result
            app._set_result_status("complete", result)
            app.action_show_export_settings()
            await pilot.pause()
            output_input = app.query_one("#output_path")
            await set_path_input_and_wait(pilot, output_input, str(tmp_path))
            output_input.focus()
            await pilot.pause()

            invalid_path = tmp_path / "not-a-real-output-directory"
            assert not invalid_path.exists()
            output_input.post_message(Paste(str(invalid_path)))
            await _wait_for_ui_state(
                pilot,
                lambda: "路徑不存在" in str(app.query_one("#output-path-status").render()),
                "invalid output path status did not refresh",
            )

            assert output_input.value == str(tmp_path)
            assert "路徑不存在" in str(app.query_one("#output-path-status").render())

    asyncio.run(run_check())


def test_paste_log_file_preserves_focused_log_file(tmp_path):
    """情境：貼上日誌檔案應保留單檔來源。"""
    log_file = tmp_path / "application.log"
    log_file.touch()

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            path_input = app.query_one("#path")
            path_input.focus()
            await pilot.pause()

            path_input.post_message(Paste(log_file.as_uri()))
            await pilot.pause()

            assert path_input.value == str(log_file)

            await pilot.press("x")
            await pilot.pause()

            assert path_input.value.startswith("x")

    asyncio.run(run_check())


def test_apply_and_load_logback_xml_updates_preview_without_changing_manual_pattern(tmp_path):
    """情境：載入 XML 應更新自動預覽而不改寫手動 Pattern。"""
    xml_file = tmp_path / "logback.xml"
    xml_file.write_text(
        '<configuration><property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/></configuration>',
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: Starting YtMamApp\n"
        "2026-05-25 15:50:34,198 WARN  [main] YtMamApp: Starting YtMamApp\n",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#log_pattern").value = "retained manual text"

            app._apply_and_load_logback_xml(str(xml_file))

            assert app.query_one("#logback_xml_path").value == str(xml_file)
            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#log_pattern").value == "retained manual text"
            assert xml_file.name in app.query_one("#pattern-source").render().plain
            assert "命中 2/2" in app.query_one("#logback-xml-status").render().plain

    asyncio.run(run_check())


@pytest.mark.parametrize("submit_via", ["run_button", "xml_input"], ids=['submit-via-run-button', 'submit-via-xml-input'])
@pytest.mark.parametrize("quote_path", [False, True], ids=['unquoted-path', 'quoted-path'])
@pytest.mark.parametrize("recipe_guard", [False, True], ids=['recipe-guard-off', 'recipe-guard-on'])
def test_pasted_logback_xml_path_loads_pattern_before_analysis(
    monkeypatch, tmp_path, submit_via, quote_path, recipe_guard
):
    """情境：不同提交方式、引號與配方狀態下貼上 XML 都應先載入 Pattern 再分析。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 INFO  [main] XmlOnlyLogger: parsed with XML pattern\n",
        encoding="utf-8",
    )
    xml_dir = tmp_path / "config"
    xml_dir.mkdir()
    xml_file = xml_dir / "logback.xml"
    xml_file.write_text(
        """<configuration>
    <property name="FILE_LOG_PATTERN" value="%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"/>
</configuration>
""",
        encoding="utf-8",
    )
    pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"
    pasted_path = f'"{xml_file}"' if quote_path else str(xml_file)
    analyzed_requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        analyzed_requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            app.query_one("#path").value = str(log_dir)
            if recipe_guard:
                app._recipe_autofill_source = app._normalize_recipe_source(str(log_dir))
            xml_input = app.query_one("#logback_xml_path")
            xml_input.focus()
            await pilot.pause()
            xml_input.post_message(Paste(pasted_path))
            await pilot.pause()

            assert xml_input.value == str(xml_file)

            if submit_via == "run_button":
                app.query_one("#run").scroll_visible(animate=False)
                await _wait_for_layout_to_settle(
                    pilot, (app.query_one("#run"), app.query_one("#form-panel"))
                )
                clicked = await pilot.click("#run")
                assert clicked, "#run click did not hit its button"
            else:
                xml_input.focus()
                await pilot.press("enter")
            await _wait_for_scheduled_analysis(app)

            assert len(analyzed_requests) == 1
            assert analyzed_requests[0].path == str(log_dir)
            assert analyzed_requests[0].log_pattern == pattern
            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#log_pattern").value == ""
            assert xml_file.name in app.query_one("#pattern-source").render().plain
            assert app._last_result is not None
            assert app._last_result.matched_occurrences == 1

    asyncio.run(run_check())


def test_analysis_preserves_nonempty_manual_logback_pattern(monkeypatch, tmp_path):
    """情境：已填寫的手動 Pattern 應在分析時保留。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 INFO  [main] com.example.Manual - manual pattern retained\n",
        encoding="utf-8",
    )
    xml_file = tmp_path / "config" / "logback.xml"
    xml_file.parent.mkdir()
    xml_file.write_text(
        '<configuration><property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/></configuration>',
        encoding="utf-8",
    )
    manual_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} - %msg%n"
    analyzed_requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        analyzed_requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#logback_xml_path").value = str(xml_file)
            app.query_one("#manual_pattern_override").value = True
            app.query_one("#log_pattern").value = manual_pattern
            await pilot.pause()

            await app.action_run_analysis()

            assert analyzed_requests[0].log_pattern == manual_pattern
            assert app.query_one("#log_pattern").value == manual_pattern
            assert manual_pattern in app.query_one("#pattern-preview").render().plain
            assert app._last_result is not None
            assert app._last_result.matched_occurrences == 1

    asyncio.run(run_check())


def test_manual_pattern_override_ignores_invalid_xml_and_uses_manual_pattern(monkeypatch, tmp_path):
    """情境：手動覆寫啟用時應忽略無效 XML 並使用手動 Pattern。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 INFO  [main] com.example.Manual - manual wins\n",
        encoding="utf-8",
    )
    malformed_xml = tmp_path / "bad-logback.xml"
    malformed_xml.write_text("<configuration>", encoding="utf-8")
    manual_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} - %msg%n"
    analyzed_requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        analyzed_requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#logback_xml_path").value = str(malformed_xml)
            app.query_one("#manual_pattern_override").value = True
            app.query_one("#log_pattern").value = manual_pattern

            await app.action_run_analysis()

            assert len(analyzed_requests) == 1
            assert analyzed_requests[0].log_pattern == manual_pattern
            assert app._last_result is not None
            assert app._last_result.matched_occurrences == 1
            assert manual_pattern in app.query_one("#pattern-preview").render().plain

    asyncio.run(run_check())


def test_explicit_xml_outside_log_directory_is_not_replaced_by_autodiscovery(monkeypatch, tmp_path):
    """情境：明確選定來源目錄外 XML 時不得被自動探索取代。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 INFO  [main] App: external XML pattern\n",
        encoding="utf-8",
    )
    local_xml = log_dir / "logback.xml"
    local_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36}: %msg%n"
    local_xml.write_text(
        f'<configuration><property name="FILE_LOG_PATTERN" value="{local_pattern}"/></configuration>',
        encoding="utf-8",
    )
    external_xml = tmp_path / "config" / "logback.xml"
    external_xml.parent.mkdir()
    external_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"
    external_xml.write_text(
        f'<configuration><property name="FILE_LOG_PATTERN" value="{external_pattern}"/></configuration>',
        encoding="utf-8",
    )
    local_candidate = patterns_module.find_best_logback_pattern(str(local_xml), str(log_dir))
    assert local_candidate is not None
    assert local_candidate.pattern == local_pattern
    assert local_candidate.matches == 1
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#logback_xml_path").value = str(external_xml)

            await app.action_run_analysis()

            assert app.query_one("#logback_xml_path").value == str(external_xml)
            assert app._last_result is not None
            assert app._last_result.analysis_request.log_pattern == external_pattern
            assert app._last_result.matched_occurrences == 1

    asyncio.run(run_check())


def test_empty_manual_pattern_fails_before_analysis(monkeypatch, tmp_path):
    """情境：手動 Pattern 空白時應在分析前失敗。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 INFO  [main] com.example.App - default works\n",
        encoding="utf-8",
    )
    analysis_calls = []
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(
        tui_module,
        "analyze_logs",
        lambda request, *, control=None: analysis_calls.append(request),
    )

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#manual_pattern_override").value = True
            app.query_one("#log_pattern").value = "  "

            await app.action_run_analysis()

            assert analysis_calls == []
            assert app._result_state == "failed"
            assert app.query_one("#result-status").render().plain

    asyncio.run(run_check())


def test_manual_text_is_ignored_when_override_is_off(monkeypatch, tmp_path):
    """情境：覆寫關閉時應忽略保留的手動文字。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 [main] INFO  com.example.App - default pattern\n",
        encoding="utf-8",
    )
    analyzed_requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        analyzed_requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#log_pattern").value = "%msg%n"
            app.query_one("#manual_pattern_override").value = False

            await app.action_run_analysis()

            assert len(analyzed_requests) == 1
            assert analyzed_requests[0].log_pattern is None
            assert app._last_result is not None
            assert app._last_result.matched_occurrences == 1
            assert "預設 Logback" in app.query_one("#pattern-source").render().plain

    asyncio.run(run_check())


def test_clearing_selected_xml_uses_default_on_next_analysis(monkeypatch, tmp_path):
    """情境：清除指定 XML 後下次分析應回到內建格式。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 [main] INFO  com.example.App - parsed by both\n",
        encoding="utf-8",
    )
    xml_file = tmp_path / "config" / "logback.xml"
    xml_file.parent.mkdir()
    xml_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
    xml_file.write_text(
        f'<configuration><property name="FILE_LOG_PATTERN" value="{xml_pattern}"/></configuration>',
        encoding="utf-8",
    )
    requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#logback_xml_path").value = str(xml_file)
            await app.action_run_analysis()
            assert requests[-1].log_pattern == xml_pattern

            app.query_one("#logback_xml_path").value = ""
            await app.action_run_analysis()

            assert len(requests) == 2
            assert requests[-1].log_pattern is None
            assert app._last_result is not None
            assert app._last_result.matched_occurrences == 1
            assert "預設 Logback" in app.query_one("#pattern-source").render().plain

    asyncio.run(run_check())


def test_pasted_logback_xml_replaces_previous_autofilled_pattern(monkeypatch, tmp_path):
    """情境：新貼上的 XML 應取代前次自動帶入的 Pattern。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 INFO  [main] XmlOnlyLogger: matched external XML\n",
        encoding="utf-8",
    )
    auto_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
    local_xml = log_dir / "logback-spring.xml"
    local_xml.write_text(
        f'<configuration><property name="FILE_LOG_PATTERN" value="{auto_pattern}"/></configuration>',
        encoding="utf-8",
    )
    external_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"
    external_xml = tmp_path / "config" / "logback.xml"
    external_xml.parent.mkdir()
    external_xml.write_text(
        f'<configuration><property name="FILE_LOG_PATTERN" value="{external_pattern}"/></configuration>',
        encoding="utf-8",
    )
    analyzed_requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        analyzed_requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            app._apply_selected_directory("path", str(log_dir))

            assert app.query_one("#logback_xml_path").value == str(local_xml)
            assert app.query_one("#log_pattern").value == ""
            assert app.query_one("#manual_pattern_override").value is False

            xml_input = app.query_one("#logback_xml_path")
            xml_input.focus()
            await pilot.pause()
            xml_input.post_message(Paste(str(external_xml)))
            await pilot.pause()

            assert xml_input.value == str(external_xml)
            await app.action_run_analysis()

            assert analyzed_requests[0].log_pattern == external_pattern
            assert app.query_one("#manual_pattern_override").value is False
            assert app._last_result is not None
            assert app._last_result.matched_occurrences == 1

    asyncio.run(run_check())


def test_default_recipe_guard_suppresses_directory_xml_discovery(monkeypatch, tmp_path):
    """情境：預設格式配方保護應阻止資料夾 XML 自動探索。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 [main] ERROR com.test.App - default recipe preserved\n",
        encoding="utf-8",
    )
    xml_file = log_dir / "logback.xml"
    xml_file.write_text(
        '<configuration><property name="FILE_LOG_PATTERN" value="%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"/></configuration>',
        encoding="utf-8",
    )
    analyzed_requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        analyzed_requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            app.query_one("#path").value = str(log_dir)
            app._recipe_autofill_source = app._normalize_recipe_source(str(log_dir))

            await app.action_run_analysis()

            assert len(analyzed_requests) == 1
            assert analyzed_requests[0].log_pattern is None
            assert app.query_one("#logback_xml_path").value == ""
            assert app.query_one("#manual_pattern_override").value is False
            assert app.query_one("#log_pattern").value == ""
            assert app._last_result is not None
            assert app._last_result.matched_occurrences == 1

    asyncio.run(run_check())


@pytest.mark.parametrize(
    ("xml_kind", "status_message"),
    [
        ("missing", "找不到檔案"),
        ("malformed", "無法解析 Logback XML"),
        ("no_pattern", "找不到可用的 Logback pattern"),
    ],
    ids=['missing-xml', 'malformed-xml', 'xml-without-pattern'],
)
def test_analysis_reports_invalid_selected_logback_xml(monkeypatch, tmp_path, xml_kind, status_message):
    """情境：指定 XML 遺失、損壞或沒有 Pattern 時應顯示對應錯誤。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-10-20 12:34:56.789 INFO  [main] XmlOnlyLogger: parsed with XML pattern\n",
        encoding="utf-8",
    )
    xml_file = tmp_path / "config" / "logback.xml"
    xml_file.parent.mkdir()
    if xml_kind == "malformed":
        xml_file.write_text("<configuration>", encoding="utf-8")
    elif xml_kind == "no_pattern":
        xml_file.write_text("<configuration />", encoding="utf-8")
    else:
        xml_file = xml_file.parent / "missing.xml"

    analyzed_requests = []
    original_analyze_logs = tui_module.analyze_logs

    def capture_request(request, *, control=None):
        analyzed_requests.append(request)
        return original_analyze_logs(request, control=control)

    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.setattr(tui_module, "analyze_logs", capture_request)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            app.query_one("#path").value = str(log_dir)
            app.query_one("#logback_xml_path").value = str(xml_file)

            await app.action_run_analysis()

            assert analyzed_requests == []
            assert app._result_state == "failed"
            xml_status = app.query_one("#logback-xml-status")
            assert xml_status.display
            assert status_message in str(xml_status.render())
            assert app.query_one("#pattern-details").collapsed

    asyncio.run(run_check())


def test_loading_view_explains_processing_state():
    """情境：載入畫面應說明處理中的狀態。"""
    panel = build_loading_view()
    lines = [str(renderable) for renderable in panel.renderable.renderables]

    assert panel.title == "執行中"
    assert "分析中，請稍候..." in lines[0]
    assert "正在掃描 Log 目錄" in lines[1]
    assert "大型資料夾" in lines[2]


def test_error_view_includes_next_step_hint():
    """情境：錯誤畫面應提供下一步提示。"""
    panel = build_error_view("找不到資料夾", "路徑不存在：/tmp/missing")
    lines = [str(renderable) for renderable in panel.renderable.renderables]

    assert panel.title == "執行失敗"
    assert "找不到資料夾" in lines[0]
    assert "路徑不存在" in lines[1]
    assert "重新選擇 Log 目錄" in lines[2]


def test_error_hint_reflects_datetime_format_errors():
    """情境：日期格式錯誤應得到對應修正提示。"""
    hint = get_error_hint("輸入錯誤", "開始日期時間格式不正確。")

    assert "日期" in hint
    assert "YYYY-MM-DD" in hint


def test_error_hint_reflects_logback_pattern_errors():
    """情境：Pattern 錯誤應得到對應修正提示。"""
    hint = get_error_hint("執行失敗", "不支援的 Logback pattern token：%replace")

    assert "Logback pattern" in hint
    assert "%replace" in hint


def test_set_result_uses_scrollable_content_width():
    """情境：結果渲染寬度應取自可捲動內容區。"""
    class FakeBox:
        def __init__(self) -> None:
            self.scrollable_content_region = SimpleNamespace(width=88)
            self.calls = []

        def clear(self) -> None:
            self.calls.append("clear")

        def write(self, renderable, width=None, expand=False):
            self.calls.append(("write", renderable, width, expand))

        def scroll_home(self) -> None:
            self.calls.append("scroll_home")

    app = LogAnalyzerApp()
    box = FakeBox()

    app._set_result(box, "renderable")

    assert box.calls[0] == "clear"
    assert box.calls[1] == ("write", "renderable", 88, True)
    assert box.calls[2] == "scroll_home"


def test_analyze_dashboard_collects_all_summaries_in_sorted_order():
    """情境：儀表板各摘要應依指定順序整理。"""
    result = SimpleNamespace(
        matched_logs=[
            {
                "count": 3,
                "timestamp": "2026-06-06 10:00:00.001",
                "logger": "com.test.Api",
                "thread": "http-1",
                "stacktrace": "java.lang.RuntimeException: boom",
                "message": "Fail A",
                "message_body": "",
            },
            {
                "count": 2,
                "timestamp": "2026-06-06 10:30:00.001",
                "logger": "com.test.Api",
                "thread": "http-2",
                "stacktrace": "java.lang.RuntimeException: boom",
                "message": "Fail B",
                "message_body": "",
            },
            {
                "count": 5,
                "timestamp": "2026-06-06 11:00:00.001",
                "logger": "com.test.Job",
                "thread": "worker-7",
                "stacktrace": "java.lang.IllegalStateException: broken",
                "message": "Fail C",
                "message_body": "",
            },
            {
                "count": 2,
                "timestamp": "2026-06-06 11:15:00.001",
                "logger": "",
                "thread": "",
                "stacktrace": "",
                "message": "Fail D",
                "message_body": "",
            },
        ]
    )

    analysis = analyze_dashboard(result)

    assert analysis.exception_summary == (
        ("java.lang.RuntimeException: boom", 2, 5),
        ("java.lang.IllegalStateException: broken", 1, 5),
    )
    assert analysis.time_hotspots == (
        ("2026-06-06 11:00", 2, 7),
        ("2026-06-06 10:00", 2, 5),
    )
    assert analysis.logger_summary == (
        ("com.test.Api", 2, 5),
        ("com.test.Job", 1, 5),
    )
    assert analysis.thread_summary == (
        ("worker-7", 1, 5),
        ("http-1", 1, 3),
        ("http-2", 1, 2),
    )


def test_analyze_dashboard_returns_empty_summaries_without_matched_logs():
    """情境：沒有匹配明細時儀表板摘要應為空。"""
    analysis = analyze_dashboard(SimpleNamespace(matched_logs=[]))

    assert analysis.exception_summary == ()
    assert analysis.time_hotspots == ()
    assert analysis.logger_summary == ()
    assert analysis.thread_summary == ()


def test_dashboard_view_includes_exception_group_summary():
    """情境：儀表板應包含例外群組摘要。"""
    result = SimpleNamespace(
        total_logs=9,
        matched_groups=3,
        matched_occurrences=7,
        format_name="csv",
        input_path="/in",
        output_path="/out/report.csv",
        exported_files=["/out/report.csv"],
        keyword="Exception",
        sort_by="time",
        ignore_case=False,
        level_summary=[("ERROR", 7)],
        matched_logs=[
            {
                "count": 4,
                "stacktrace": "   1: java.lang.RuntimeException: boom\n   2: at com.test.App.main",
                "message": "Fail A",
                "message_body": "",
            },
            {
                "count": 2,
                "stacktrace": "   8: java.lang.RuntimeException: boom\n   9: at com.test.Service.run",
                "message": "Fail B",
                "message_body": "",
            },
            {
                "count": 1,
                "stacktrace": "   3: java.lang.IllegalStateException: broken\n   4: at com.test.Other.main",
                "message": "Fail C",
                "message_body": "",
            },
        ],
    )

    console = Console(width=120, record=True, color_system=None)
    console.print(build_dashboard_view(result, compact=False))
    output = console.export_text()

    assert "例外群組摘要" in output
    assert "java.lang.RuntimeException: boom" in output
    assert "2 群組" in output
    assert "6 次" in output


def test_dashboard_view_includes_time_hotspot_summary():
    """情境：儀表板應包含時間熱點摘要。"""
    result = SimpleNamespace(
        total_logs=12,
        matched_groups=4,
        matched_occurrences=10,
        format_name="csv",
        input_path="/in",
        output_path="/out/report.csv",
        exported_files=["/out/report.csv"],
        keyword="Exception",
        sort_by="time",
        ignore_case=False,
        level_summary=[("ERROR", 10)],
        matched_logs=[
            {
                "count": 4,
                "timestamp": "2026-06-06 10:00:00.001",
                "stacktrace": "",
                "message": "Fail A",
                "message_body": "",
            },
            {
                "count": 2,
                "timestamp": "2026-06-06 10:30:00.001",
                "stacktrace": "",
                "message": "Fail B",
                "message_body": "",
            },
            {
                "count": 3,
                "timestamp": "2026-06-06 11:00:00.001",
                "stacktrace": "",
                "message": "Fail C",
                "message_body": "",
            },
        ],
    )

    console = Console(width=120, record=True, color_system=None)
    console.print(build_dashboard_view(result, compact=False))
    output = console.export_text()

    assert "時間熱點" in output
    assert "2026-06-06 10:00" in output
    assert "2026-06-06 11:00" in output
    assert "2 群組" in output
    assert "6 次" in output


def test_dashboard_view_includes_logger_thread_distribution():
    """情境：儀表板應包含 Logger 與 Thread 分布。"""
    result = SimpleNamespace(
        total_logs=8,
        matched_groups=3,
        matched_occurrences=8,
        format_name="csv",
        input_path="/in",
        output_path="/out/report.csv",
        exported_files=["/out/report.csv"],
        keyword="Exception",
        sort_by="time",
        ignore_case=False,
        level_summary=[("ERROR", 8)],
        matched_logs=[
            {
                "count": 3,
                "timestamp": "2026-06-06 10:00:00.001",
                "logger": "com.test.Api",
                "thread": "http-1",
                "stacktrace": "",
                "message": "Fail A",
                "message_body": "",
            },
            {
                "count": 2,
                "timestamp": "2026-06-06 10:30:00.001",
                "logger": "com.test.Api",
                "thread": "http-1",
                "stacktrace": "",
                "message": "Fail B",
                "message_body": "",
            },
            {
                "count": 3,
                "timestamp": "2026-06-06 11:00:00.001",
                "logger": "com.test.Job",
                "thread": "worker-7",
                "stacktrace": "",
                "message": "Fail C",
                "message_body": "",
            },
        ],
    )

    console = Console(width=120, record=True, color_system=None)
    console.print(build_dashboard_view(result, compact=False))
    output = console.export_text()

    assert "Logger / Thread 分布" in output
    assert "com.test.Api" in output
    assert "http-1" in output
    assert "worker-7" in output
    assert "2 群組" in output
    assert "5 次" in output


def test_dashboard_view_shows_summary_before_detail_panels():
    """情境：儀表板應先呈現摘要再呈現細節。"""
    result = SimpleNamespace(
        total_logs=8,
        matched_groups=3,
        matched_occurrences=8,
        format_name="csv",
        input_path="/in",
        output_path="/out/report.csv",
        exported_files=["/out/report.csv"],
        keyword="Exception",
        sort_by="time",
        ignore_case=False,
        level_summary=[("ERROR", 8)],
        matched_logs=[
            {
                "count": 3,
                "timestamp": "2026-06-06 10:00:00.001",
                "logger": "com.test.Api",
                "thread": "http-1",
                "stacktrace": "java.lang.RuntimeException: boom",
                "message": "Fail A",
                "message_body": "",
            },
            {
                "count": 2,
                "timestamp": "2026-06-06 10:30:00.001",
                "logger": "com.test.Api",
                "thread": "http-1",
                "stacktrace": "",
                "message": "Fail B",
                "message_body": "",
            },
        ],
    )

    console = Console(width=120, record=True, color_system=None)
    console.print(build_dashboard_view(result, compact=False))
    output = console.export_text()

    assert output.index("分析資訊") < output.index("例外群組摘要")
    assert output.index("Level 分布") < output.index("例外群組摘要")


def test_dashboard_view_renders_all_sections_in_order_for_both_layouts(monkeypatch):
    """情境：兩種舊版面旗標下儀表板各區順序應一致。"""
    result = SimpleNamespace(
        total_logs=8,
        matched_groups=3,
        matched_occurrences=8,
        format_name="csv",
        input_path="/in",
        output_path="/out/report.csv",
        exported_files=["/out/report.csv"],
        keyword="Exception",
        sort_by="time",
        ignore_case=False,
        level_summary=[("ERROR", 8)],
        matched_logs=[{"count": 8}],
    )

    analysis = tui_views.DashboardAnalysis(
        exception_summary=(("java.lang.RuntimeException: boom", 1, 8),),
        time_hotspots=(("2026-06-06 10:00", 1, 8),),
        logger_summary=(("com.test.Api", 1, 8),),
        thread_summary=(("http-1", 1, 8),),
    )
    calls = []

    def fake_analyze_dashboard(actual_result):
        calls.append(actual_result)
        return analysis

    monkeypatch.setattr(tui_views, "analyze_dashboard", fake_analyze_dashboard)

    expected_sections = (
        "執行狀態",
        "總 Log",
        "群組",
        "命中",
        "分析資訊",
        "Level 分布",
        "例外群組摘要",
        "時間熱點",
        "Logger / Thread 分布",
    )
    outputs = {}
    for compact in (False, True):
        view = build_dashboard_view(result, compact=compact)
        console = Console(width=120, record=True, color_system=None)
        console.print(view)
        output = console.export_text()
        outputs[compact] = output

        positions = [output.index(section) for section in expected_sections]
        assert positions == sorted(positions)
        assert output.index("已匯出 CSV") < output.index("總 Log")
        metrics_line = next(line for line in output.splitlines() if "總 Log" in line)
        assert "群組" in metrics_line and "命中" in metrics_line

    assert outputs[False] == outputs[True]
    assert calls == [result, result]


def test_apply_selected_directory_autofills_logback_settings(monkeypatch, tmp_path):
    """情境：選取資料夾應自動帶入 Logback 設定。"""
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
        "#source-scope-preview": _FakeSourceScopePreview(),
        "#logback_xml_path": FakeInput(),
        "#log_pattern": FakeInput(),
        "#manual_pattern_override": SimpleNamespace(value=False),
        "#logback-xml-status": FakeStatic(),
        "#pattern-source": FakeStatic(),
        "#pattern-preview": FakeStatic(),
    }

    monkeypatch.setattr("log_analyzer.presentation.tui.controllers.patterns.find_best_logback_pattern", lambda *_args, **_kwargs: FakePattern())

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    app._apply_selected_directory("path", str(log_dir))

    assert mapping["#logback_xml_path"].value == str(xml_file)
    assert mapping["#log_pattern"].value == ""
    assert mapping["#manual_pattern_override"].value is False
    assert "%d %-5level [%thread] %logger{36} - %msg%n" in mapping["#pattern-preview"].value
    assert "已自動載入" in mapping["#logback-xml-status"].value
    assert xml_file.name in mapping["#logback-xml-status"].value


def test_apply_selected_directory_does_not_override_manual_logback_values(monkeypatch, tmp_path):
    """情境：選取資料夾不得覆蓋手動 Logback 設定。"""
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
        "#source-scope-preview": _FakeSourceScopePreview(),
        "#logback_xml_path": FakeInput("/manual/logback.xml"),
        "#log_pattern": FakeInput("manual pattern"),
        "#manual_pattern_override": SimpleNamespace(value=False),
        "#logback-xml-status": FakeStatic(),
        "#pattern-source": FakeStatic(),
        "#pattern-preview": FakeStatic(),
    }

    monkeypatch.setattr("log_analyzer.presentation.tui.controllers.patterns.find_best_logback_pattern", fail_if_called)

    app = LogAnalyzerApp()
    app.query_one = lambda selector, *_args, **_kwargs: mapping[selector]  # type: ignore[assignment]

    app._apply_selected_directory("path", str(log_dir))

    assert mapping["#logback_xml_path"].value == "/manual/logback.xml"
    assert mapping["#log_pattern"].value == "manual pattern"
    assert mapping["#manual_pattern_override"].value is False
    assert mapping["#logback-xml-status"].value == ""


def test_main_panels_have_equal_width_in_wide_layout():
    """情境：寬視窗下輸入與結果面板應等寬。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()

            form_panel = app.query_one("#form-panel")
            output_panel = app.query_one("#output-panel")

            assert app.query_one("#content").has_class("wide")
            assert form_panel.region.width == output_panel.region.width
            assert output_panel.region.x > form_panel.region.x + form_panel.region.width

    asyncio.run(run_check())


def test_main_panels_switch_to_compact_before_either_panel_is_too_narrow():
    """情境：任何面板過窄前應切換成直向排列。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            form_panel = app.query_one("#form-panel")
            output_panel = app.query_one("#output-panel")

            assert app.query_one("#content").has_class("compact")
            assert output_panel.region.y > form_panel.region.y

    asyncio.run(run_check())


def test_run_button_stays_below_form_scroll_area():
    """情境：表單捲動後開始分析按鈕仍應位於捲動區之外。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_SCROLL_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            form_panel = app.query_one("#form-panel")
            form_actions = app.query_one("#form-actions")
            app.action_toggle_advanced_settings()
            await _wait_for_layout_to_settle(pilot, (form_panel, form_actions))
            form_panel.scroll_end(animate=False)
            await _wait_for_layout_to_settle(
                pilot, (form_panel, form_actions), lambda: form_panel.scroll_offset.y > 0
            )

            assert form_panel.has_class("wide")
            assert app.query_one("#advanced-settings").has_class("advanced-wide")
            assert form_panel.scroll_offset.y > 0
            assert form_actions.region.y >= form_panel.region.y + form_panel.region.height

    asyncio.run(run_check())


def test_clear_button_is_in_output_panel_bottom_actions():
    """情境：清除結果按鈕應在輸出面板底部操作區。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            output_panel = app.query_one("#output-panel")
            clear_button = app.query_one("#clear")

            assert clear_button.parent.id == "output-actions"
            assert output_panel.region.y <= clear_button.region.y < output_panel.region.y + output_panel.region.height

    asyncio.run(run_check())


def test_run_and_clear_buttons_share_the_same_row_in_wide_layout():
    """情境：寬版開始分析與清除結果應位於同一列。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()

            run_button = app.query_one("#run")
            clear_button = app.query_one("#clear")

            assert run_button.region.y == clear_button.region.y

    asyncio.run(run_check())


def test_compact_form_actions_stay_above_output_panel_and_align_with_form_controls():
    """情境：窄版表單操作應在結果面板上方並與欄位對齊。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            form_panel = app.query_one("#form-panel")
            form_actions = app.query_one("#form-actions")
            output_panel = app.query_one("#output-panel")
            toggle = app.query_one("#toggle_advanced_settings")
            run_button = app.query_one("#run")

            assert form_actions.region.y >= form_panel.region.y + form_panel.region.height
            assert output_panel.region.y > form_actions.region.y + form_actions.region.height
            assert toggle.region.x + toggle.region.width == run_button.region.x + run_button.region.width

    asyncio.run(run_check())


def test_compact_output_panel_keeps_result_box_visible_with_bottom_actions():
    """情境：窄版結果面板應保留結果區與底部按鈕可見。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()

            result_box = app.query_one("#result-box")

            assert result_box.region.height > 0

    asyncio.run(run_check())


def test_result_status_starts_idle_and_disables_clear():
    """情境：初始結果狀態應為閒置且停用清除。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            assert str(app.query_one("#result-status").render()) == "狀態：尚未分析"
            assert app.query_one("#clear").disabled is True

    asyncio.run(run_check())


def test_result_status_shows_completion_summary_and_clear_returns_to_idle(tmp_path):
    """情境：分析完成應顯示摘要而清除後回到閒置。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            result = replace(make_analysis_result(tmp_path), total_logs=12, matched_occurrences=5)

            app._last_result = result
            app._set_result_status("complete", result)

            assert str(app.query_one("#result-status").render()) == "狀態：完成｜總 Log 12｜命中 5"
            assert app.query_one("#clear").disabled is False

            app.action_clear_result()

            assert str(app.query_one("#result-status").render()) == "狀態：尚未分析"
            assert app.query_one("#clear").disabled is True

    asyncio.run(run_check())


def test_result_status_marks_failure_and_removes_duplicate_helper_text():
    """情境：分析失敗應標記失敗並移除重複說明。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            app._set_result_status("failed")

            assert str(app.query_one("#result-status").render()) == "狀態：失敗"
            assert app.query_one("#clear").disabled is True
            assert not app.query(".helper")

    asyncio.run(run_check())


def _stored_result(path: Path, group: dict[str, object]) -> SimpleNamespace:
    with path.open("wb") as file_handle:
        pickle.dump(group, file_handle)
    return SimpleNamespace(
        total_logs=1,
        matched_occurrences=1,
        matched_groups=1,
        input_path=str(path.parent),
        source_states={},
        matched_logs=MatchedLogsStore(path=str(path), length=1),
        level_summary=[(str(group["level"]), int(group.get("count", 1)))],
        dashboard_summary=analyze_dashboard(SimpleNamespace(matched_logs=[group])),
    )


def _event_group() -> dict[str, object]:
    occurrence = {
        "filename": "sample.log",
        "line_number": 1,
        "timestamp": "2026-10-20 12:00:00",
        "thread": "main",
    }
    return {
        "timestamp": "2026-10-20 12:00:00",
        "last_timestamp": "2026-10-20 12:00:00",
        "thread": "main",
        "level": "ERROR",
        "logger": "com.example.Service",
        "filename": "sample.log",
        "occurrences": [occurrence],
        "message": "failure",
        "message_body": "failure",
        "stacktrace": "",
        "stacktrace_lines": [],
        "count": 1,
        "line_numbers": [1],
    }


def test_dashboard_browse_button_opens_event_browser():
    """情境：查看原文操作應開啟事件瀏覽。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            result = SimpleNamespace(
                total_logs=1,
                matched_occurrences=1,
                matched_groups=1,
                input_path=".",
                source_states={},
                matched_logs=[_event_group()],
            )
            app._last_result = result
            app._set_result_status("complete", result)

            await pilot.click("#browse-events")
            await pilot.pause()

            assert isinstance(app.screen, EventBrowserScreen)
            assert len(app.screen.query_one("#event-options").options) == 1

    asyncio.run(run_check())


def test_result_replacement_clear_and_unmount_release_stores(tmp_path: Path, monkeypatch) -> None:
    """情境：替換、清除結果與卸載 App 時應釋放明細儲存器。"""
    old_path = tmp_path / "old-groups.pickle"
    new_path = tmp_path / "new-groups.pickle"
    failed_path = tmp_path / "failed-groups.pickle"
    old_result = _stored_result(old_path, _event_group())
    new_result = _stored_result(new_path, _event_group())
    failed_result = _stored_result(failed_path, _event_group())
    analysis_calls = []

    def fake_analyze_logs(request, *, control=None):
        analysis_calls.append((old_path.exists(), request))
        return new_result

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui_module, "analyze_logs", fake_analyze_logs)
    monkeypatch.setattr(results_module, "build_dashboard_view", lambda *_args, **_kwargs: "dashboard")

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._last_result = old_result

            await app.action_run_analysis()

            assert len(analysis_calls) == 1
            assert analysis_calls[0][0] is True
            assert isinstance(analysis_calls[0][1], AnalysisRequest)
            assert analysis_calls[0][1].path == str(tmp_path)
            assert app._last_result is new_result
            assert new_path.exists()
            assert not old_path.exists()

            browser = EventBrowserScreen(new_result)
            app.push_screen(browser)
            await pilot.pause()
            app.action_clear_result()
            await pilot.pause()

            assert not new_path.exists()
            assert app._last_result is None
            assert app.screen is not browser

            app._last_result = failed_result
            monkeypatch.setattr(
                tui_module,
                "analyze_logs",
                lambda _request, *, control=None: (_ for _ in ()).throw(PermissionError("denied")),
            )
            await app.action_run_analysis()
            assert failed_path.exists()
            assert app._last_result is failed_result
            app.action_clear_result()
            assert not failed_path.exists()
            assert app._last_result is None

            shutdown_path = tmp_path / "shutdown-groups.pickle"
            shutdown_result = _stored_result(shutdown_path, _event_group())
            app._last_result = shutdown_result

        assert not shutdown_path.exists()

    asyncio.run(run_check())


def test_time_range_inputs_are_not_collapsed_in_wide_layout():
    """情境：寬版時間區間欄位不應塌縮。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            app.action_toggle_advanced_settings()
            await pilot.pause()
            app.query_one("#time-preset").value = "custom"
            await _wait_for_ui_state(
                pilot,
                lambda: app.query_one("#time-range-custom").display
                and all(
                    app.query_one(f"#{field_id}").region.height > 0
                    for field_id in ("start_date", "start_time", "end_date", "end_time")
                ),
                "custom time fields did not finish expanding before width checks",
            )

            time_section = app.query_one(".time-section")
            await _wait_for_ui_state(
                pilot,
                lambda: time_section.region.width > 0,
                "time range section did not enter the layout",
            )
            await _wait_for_layout_to_settle(
                pilot,
                (time_section, *(app.query_one(selector) for selector in (
                    "#start_date", "#start_time", "#end_date", "#end_time"
                ))),
            )
            assert app.query_one("#advanced-settings").has_class("advanced-wide")
            assert time_section.region.width > 20

            for selector in ("#start-date-group", "#start-time-group", "#end-date-group", "#end-time-group"):
                group = app.query_one(selector)
                assert group.region.width > 10

            for selector in ("#start_date", "#start_time", "#end_date", "#end_time"):
                field = app.query_one(selector)
                assert field.region.width > 10

    asyncio.run(run_check())


def test_tab_order_follows_vertical_form_flow_when_advanced_settings_are_hidden(monkeypatch, tmp_path):
    """情境：隱藏進階設定時 Tab 順序應依畫面由上而下。"""
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            await _wait_for_ui_state(
                pilot,
                lambda: app.focused is None,
                "initial focus reset did not complete",
            )

            sequence = []
            for _ in range(12):
                focused = app.focused
                sequence.append(focused.id if focused and getattr(focused, "id", None) else None)
                await _press_tab_and_wait_for_focus(pilot)

            assert sequence == [
                None,
                "analysis-mode",
                "path",
                "browse_path",
                "preview-format",
                "clear-condition-levels",
                "select-source-files",
                "keyword",
                "clear_keyword",
                "toggle_advanced_settings",
                "manage-recipes",
                "run",
            ]

    asyncio.run(run_check())


def test_tab_order_follows_vertical_form_flow_when_advanced_settings_are_visible(monkeypatch, tmp_path):
    """情境：顯示進階設定時 Tab 順序應依畫面由上而下。"""
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    monkeypatch.chdir(tmp_path)
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()
            app.query_one("#manual_pattern_override").value = True
            app.action_toggle_advanced_settings()
            advanced = app.query_one("#advanced-settings")
            await _wait_for_layout_to_settle(
                pilot,
                (advanced,),
                lambda: advanced.display
                and advanced.has_class("advanced-wide")
                and advanced.content_size.width > 0,
            )
            app.query_one("#time-preset").value = "custom"
            await _wait_for_ui_state(
                pilot,
                lambda: app.query_one("#time-range-custom").display
                and all(
                    app.query_one(f"#{field_id}").region.height > 0
                    for field_id in ("start_date", "start_time", "end_date", "end_time")
                ),
                "custom time fields did not finish expanding before Tab checks",
            )

            assert app.query_one("#advanced-settings").has_class("advanced-wide")
            sequence = []
            for _ in range(22):
                focused = app.focused
                sequence.append(focused.id if focused and getattr(focused, "id", None) else None)
                await _press_tab_and_wait_for_focus(pilot)

            assert sequence == [
                None,
                "analysis-mode",
                "path",
                "browse_path",
                "preview-format",
                "clear-condition-levels",
                "clear-condition-pattern",
                "select-source-files",
                "keyword",
                "clear_keyword",
                "toggle_advanced_settings",
                "logback_xml_path",
                "browse_logback_xml",
                "manual_pattern_override",
                "log_pattern",
                "time-preset",
                "start_date",
                "start_time",
                "end_date",
                "end_time",
                "ignore_case",
                "sort_by",
            ]

    asyncio.run(run_check())


def test_form_panel_scroll_area_reaches_right_edge():
    """情境：表單捲動區應延伸至面板右緣。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()

            form_panel = app.query_one("#form-panel")
            form_right = form_panel.region.x + form_panel.region.width
            content_right = form_panel.content_region.x + form_panel.content_region.width

            assert form_right - content_right <= 1

    asyncio.run(run_check())


def test_form_controls_keep_gap_from_scrollbar(tmp_path):
    """情境：表單控制元件應與捲軸保持間距。"""
    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=WIDE_TERMINAL_SIZE) as pilot:
            await pilot.pause()

            form_panel = app.query_one("#form-panel")
            content_right = form_panel.content_region.x + form_panel.content_region.width

            result = make_analysis_result(tmp_path)
            app._last_result = result
            app._set_result_status("complete", result)
            app.action_show_export_settings()
            await pilot.pause()

            for selector in ("#clear_keyword", "#format"):
                control = app.query_one(selector)
                control_right = control.region.x + control.region.width
                assert content_right - control_right >= 2

    asyncio.run(run_check())


def test_refresh_output_name_default_updates_only_auto_value(monkeypatch):
    """情境：更新預設輸出名稱只應改動仍由系統自動命名的值。"""
    class FakeInput:
        def __init__(self, value: str) -> None:
            self.value = value

    monkeypatch.setattr(
        "log_analyzer.presentation.tui.app.get_default_output_name_text",
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
    """情境：進階設定切換應同步顯示狀態與按鈕文字。"""
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
