from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from functools import partial
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.events import Paste, Resize
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RichLog, Select, Static

from ..application.analysis_service import (
    DEFAULT_SELECTED_LEVELS,
    AnalysisRequest,
    AnalysisResult,
    NoMatchingLogsError,
    ResultExportOptions,
    analyze_logs,
    build_output_path,
    export_analysis_result,
)
from ..application.evidence_manifest import export_evidence_manifest
from ..domain.logback_pattern import DEFAULT_LOGBACK_PATTERN
from ..domain.logback_xml import find_best_logback_pattern
from ..infrastructure.export_fields import EXPORT_FIELDS, EXPORT_FIELD_LABELS
from ..infrastructure.recipe_store import RECIPE_FIELDS, RecipeStore
from .tui_dialogs import DirectoryPickerScreen, FilePickerScreen, ShortcutInput
from .tui_event_browser import EventBrowserScreen
from .tui_recipes import RecipeManagerScreen
from .recent_form_state import load_recent_tui_state, save_recent_tui_state
from .tui_inputs import (
    get_default_end_date_text,
    get_default_end_time_text,
    get_default_output_name_text,
    get_default_start_date_text,
    get_default_start_time_text,
    parse_datetime_range_inputs,
)
from .tui_views import (
    build_dashboard_view,
    build_error_view,
    build_idle_view,
    build_loading_view,
    build_zero_result_view,
    format_path_status,
)
from ..infrastructure.paths import get_system_root_path, inspect_directory_path, validate_pasted_path
from ..version import get_package_version

__all__ = ["get_system_root_path"]

logger = logging.getLogger(__name__)


def _export_reports_and_manifest(
    result: AnalysisResult,
    options: ResultExportOptions,
    include_manifest: bool,
) -> tuple[list[str], str | None, Exception | None]:
    report_files = export_analysis_result(result, options)
    if not include_manifest:
        return report_files, None, None
    try:
        manifest_path = export_evidence_manifest(result, options, report_files)
    except Exception as exc:  # noqa: BLE001 - reports succeeded and remain a retryable partial success
        return report_files, None, exc
    return report_files, manifest_path, None


class LogAnalyzerApp(App):
    TITLE = "Java Log Analyzer"
    _PACKAGE_ROOT = Path(__file__).resolve().parents[1]
    CSS_PATH = [
        str(_PACKAGE_ROOT / "tcss" / "tui.base.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.layout.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.dialog.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.responsive.layout.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.responsive.form.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.responsive.advanced.tcss"),
    ]
    _last_result: AnalysisResult | None = None
    _result_state = "idle"
    _advanced_settings_visible: bool = False
    _auto_logback_xml_path: str = ""
    _auto_log_pattern: str = ""
    _last_logback_autofill_source: str = ""
    BINDINGS = [
        ("q", "quit", "離開"),
        ("enter", "run_analysis", "開始分析"),
        ("c", "clear_result", "清除結果"),
        ("a", "toggle_advanced_settings", "進階設定"),
    ]

    def compose(self) -> ComposeResult:
        self._auto_output_name = get_default_output_name_text()
        yield Header(show_clock=True)
        with Container(id="shell"):
            yield from self._compose_hero()

            with ScrollableContainer(id="content", can_focus=False):
                with Container(id="form-column"):
                    with ScrollableContainer(id="form-panel", can_focus=False):
                        yield from self._compose_basic_input_fields()
                        yield from self._compose_advanced_settings()
                        yield from self._compose_export_settings()
                    yield from self._compose_actions()

                yield from self._compose_result_panel()

        yield Footer()

    def _compose_hero(self) -> ComposeResult:
        with Container(id="hero"), Container(id="hero-title-row"):
            yield Static("Java Log Analyzer", id="hero-title")
            yield Static(f"v{get_package_version()}", id="hero-version")
            yield Static("·", id="hero-separator")
            yield Static("分析、篩選並匯出 Java Logback 記錄", id="hero-subtitle")

    def _compose_basic_input_fields(self) -> ComposeResult:
        yield Static("輸入設定", classes="section-title")

        with Container(classes="field"):
            yield Label("Log 目錄")
            with Container(classes="path-row"):
                yield ShortcutInput(placeholder="例如：./logs", value=".", id="path")
                yield Button("瀏覽", id="browse_path")
        yield Static("", classes="path-status", id="path-status")

        with Container(classes="field"):
            yield Label("關鍵字")
            with Container(classes="path-row"):
                yield ShortcutInput(placeholder="例如：Order_123 或 SQLException", id="keyword")
                yield Button("清除", id="clear_keyword")
        yield Static("", classes="field-hint")

        yield Button("展開進階設定", id="toggle_advanced_settings", classes="advanced-toggle")
        yield Static("", classes="field-hint")

    def _compose_advanced_settings(self) -> ComposeResult:
        with Container(id="advanced-settings", classes="advanced-settings"):
            with Container(id="advanced-settings-title-row"):
                yield Static("進階設定", classes="section-title section-title--sub")

            yield from self._compose_advanced_output_settings()
            yield from self._compose_time_settings()
            yield from self._compose_display_settings()
            yield from self._compose_level_settings()

    def _compose_advanced_output_settings(self) -> ComposeResult:
        with Container(classes="field"):
            yield Label("Logback XML")
            with Container(classes="path-row compact-buttons"):
                yield ShortcutInput(placeholder="例如：./logback-spring.xml", id="logback_xml_path")
                yield Button("瀏覽", id="browse_logback_xml")
        yield Static("", classes="field-hint", id="logback-xml-status")

        with Container(classes="field"):
            yield Label("Log 格式")
            yield Select(
                [("預設 Logback", "default"), ("進階 Pattern", "custom")],
                value="default",
                id="pattern_mode",
            )
        yield Static("", classes="field-hint")

        with Container(classes="field", id="pattern-field"):
            yield Label("Pattern")
            with Container(classes="field-body"):
                yield ShortcutInput(placeholder=DEFAULT_LOGBACK_PATTERN, id="log_pattern")
                yield Static("進階模式才會套用；僅支援常見 Logback token", classes="field-hint")

    def _compose_time_settings(self) -> ComposeResult:
        with Container(classes="time-section"):
            yield Label("時間區間")
            with Container(classes="time-stack"):
                with Container(id="start-time-row", classes="time-row"):
                    with Container(id="start-date-group", classes="time-group"):
                        yield Label("開始日期")
                        yield ShortcutInput(
                            value=get_default_start_date_text(),
                            placeholder="YYYY-MM-DD",
                            id="start_date",
                        )
                    with Container(id="start-time-group", classes="time-group"):
                        yield Label("開始時間")
                        yield ShortcutInput(
                            value=get_default_start_time_text(),
                            placeholder="HH:MM",
                            id="start_time",
                        )
                with Container(id="end-time-row", classes="time-row"):
                    with Container(id="end-date-group", classes="time-group"):
                        yield Label("結束日期")
                        yield ShortcutInput(
                            value=get_default_end_date_text(),
                            placeholder="YYYY-MM-DD",
                            id="end_date",
                        )
                    with Container(id="end-time-group", classes="time-group"):
                        yield Label("結束時間")
                        yield ShortcutInput(
                            value=get_default_end_time_text(),
                            placeholder="HH:MM",
                            id="end_time",
                        )
        yield Static("", classes="field-hint")

    def _compose_display_settings(self) -> ComposeResult:
        with Container(classes="field-row"), Container(classes="field field--inline field--ignore"):
            yield Label("忽略大小寫")
            yield Checkbox(value=True, id="ignore_case")

        with Container(classes="field-row"):
            with Container(classes="field field--inline field--sort"):
                yield Label("排序方式")
                yield Select(
                    [("時間排序", "time"), ("Level 分組", "level")],
                    value="time",
                    id="sort_by",
                )

            with Container(classes="field field--inline field--display-mode"):
                yield Label("顯示模式")
                yield Select(
                    [("預設摘要", "full"), ("濃縮摘要", "summary")],
                    value="summary",
                    id="display_mode",
                )
        yield Static("", classes="field-hint")

    def _compose_export_settings(self) -> ComposeResult:
        with Container(id="export-settings", classes="export-settings"):
            yield Static("匯出設定", classes="section-title")
            with Container(classes="field"):
                yield Label("目標資料夾")
                with Container(classes="path-row"):
                    yield ShortcutInput(placeholder="例如：./exports", value=".", id="output_path")
                    yield Button("瀏覽", id="browse_output_path")
                yield Static("", classes="path-status", id="output-path-status")

            with Container(classes="field"):
                yield Label("輸出檔名")
                with Container(classes="field-body"):
                    yield ShortcutInput(
                        placeholder="例如：analysis_123000",
                        value=self._auto_output_name,
                        id="output_name",
                    )
                    yield Static("副檔名由輸出格式自動決定", classes="field-hint")

            with Container(classes="field-row"):
                with Container(classes="field field--inline field--format"):
                    yield Label("輸出格式")
                    yield Select(
                        [("CSV (Excel)", "csv"), ("JSON", "json"), ("Markdown", "md")],
                        value="csv",
                        id="format",
                    )
                with Container(classes="field field--inline field--split-output"):
                    yield Label("依 Level 分檔")
                    yield Checkbox(value=False, id="split_by_level")

            yield Label("輸出欄位", classes="field-label")
            with Container(id="export-fields", classes="export-fields"):
                for field in EXPORT_FIELDS:
                    yield Checkbox(
                        EXPORT_FIELD_LABELS[field],
                        value=True,
                        id=f"export_field_{field}",
                    )

            yield Static("", id="export-status", classes="export-status", markup=False)
            yield Checkbox("包含證據清單（JSON）", value=False, id="include_manifest")
            yield Static(
                "證據清單包含分析條件、來源核對與報表雜湊，不含日誌內容快照。來源核對比對掃描時記錄的檔案大小與修改時間，以及匯出時計算雜湊前後的檔案大小與修改時間；不證明掃描時的日誌內容不可變。",
                classes="field-hint",
                id="manifest-hint",
                markup=False,
            )
            yield Button("執行匯出", variant="primary", id="confirm-export")

    def _compose_level_settings(self) -> ComposeResult:
        with Container(classes="field"):
            yield Label("Log 層級")
            with Container(classes="field-body"):
                with Container(classes="level-filter-row"):
                    for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
                        yield Checkbox(level, value=level in DEFAULT_SELECTED_LEVELS, id=f"level_{level}")
                yield Checkbox("不限 Level", value=False, id="level_unrestricted")
                with Container(classes="level-filter-actions"):
                    yield Button("全選", id="select_all_levels")
                    yield Button("清空", id="clear_levels")
                yield Static("預設分析 ERROR / WARN / INFO", classes="field-hint")

    def _compose_actions(self) -> ComposeResult:
        with Container(id="form-actions"):
            yield Button("管理分析配方", id="manage-recipes")
            yield Button("開始分析", variant="primary", id="run")
        yield Static("", id="recipe-feedback", classes="recipe-feedback", markup=False)

    def _compose_result_panel(self) -> ComposeResult:
        with Container(id="output-panel"):
            yield Static("執行結果", classes="section-title")
            yield Static("", id="result-status")
            yield RichLog(
                id="result-box",
                markup=True,
                highlight=False,
                auto_scroll=False,
                wrap=True,
                min_width=0,
            )
            with Container(id="zero-result-actions", classes="zero-results-hidden"):
                yield Button("清除時間並重試", id="retry_without_time", disabled=True)
                yield Button("不限 Level 並重試", id="retry_without_level", disabled=True)
                yield Button("清除關鍵字並重試", id="retry_without_keyword", disabled=True)
            with Container(id="output-actions"):
                yield Button("查看事件原文", id="browse-events", disabled=True)
                yield Button("匯出結果", id="export-result", disabled=True)
                yield Button("清除結果", id="clear")

    def on_mount(self) -> None:
        path_input = self.query_one("#path", Input)
        output_path_input = self.query_one("#output_path", Input)
        self.query_one("#result-box", RichLog).can_focus = False
        self._restore_recent_form_state()
        self._sync_pattern_field_visibility()
        self.call_after_refresh(self.set_focus, None)
        self._set_advanced_settings_visible(False)
        self._set_export_settings_visible(False)
        self._sync_layout(self.size.width, self.size.height)
        self._set_result_status("idle")
        self.update_path_preview(path_input.value)
        self.update_path_preview(output_path_input.value, "#output-path-status", require_writable=True)

    def on_unmount(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            self._release_result_when_operation_finishes = True
        else:
            self._release_last_result(close_browser=False)

    def on_resize(self, event: Resize) -> None:
        self._sync_layout(event.size.width, event.size.height)

        last_result = getattr(self, "_last_result", None)
        if last_result is not None:
            result_box = self.query_one("#result-box", RichLog)
            self._set_result(
                result_box,
                build_dashboard_view(last_result, self._should_compact_dashboard(self.size.width, self.size.height)),
            )
            return

        self.update_path_preview(self.query_one("#path", Input).value)

    def _sync_layout(self, width: int, height: int) -> None:
        if height <= 0:
            return

        compact = self._is_compact(width, height)
        form_panel = self.query_one("#form-panel", ScrollableContainer)
        form_actions = self.query_one("#form-actions", Container)
        content = self.query_one("#content", ScrollableContainer)
        hero_title_row = self.query_one("#hero-title-row", Container)

        if compact:
            content.remove_class("wide")
            content.add_class("compact")
            form_panel.remove_class("wide")
            form_panel.add_class("compact")
            form_actions.remove_class("wide")
            form_actions.add_class("compact")
            hero_title_row.remove_class("wide")
            hero_title_row.add_class("compact")
        else:
            content.remove_class("compact")
            content.add_class("wide")
            form_panel.remove_class("compact")
            form_panel.add_class("wide")
            form_actions.remove_class("compact")
            form_actions.add_class("wide")
            hero_title_row.remove_class("compact")
            hero_title_row.add_class("wide")

    def _is_compact(self, width: int, height: int) -> bool:
        return width < 112 or height < 18

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self.is_running:
            return
        if event.input.id == "path":
            if self._normalize_recipe_source(event.value) != getattr(self, "_recipe_autofill_source", ""):
                self._recipe_autofill_source = ""
            self.update_path_preview(event.value)
        elif event.input.id == "output_path":
            self.update_path_preview(event.value, "#output-path-status", require_writable=True)

    def on_paste(self, event: Paste) -> None:
        """只在路徑欄位接收終端拖放所產生的貼上內容。"""
        focused = self.focused
        if not isinstance(focused, Input) or not self.handle_path_paste(focused, event.text):
            return
        event.stop()

    def handle_path_paste(self, path_input: Input, pasted_text: str) -> bool:
        """驗證並套用路徑欄位的貼上內容；回傳是否已處理事件。"""
        if path_input.id not in {"path", "output_path", "logback_xml_path"}:
            return False

        is_valid, normalized_path, message = validate_pasted_path(pasted_text, path_input.id)
        if not is_valid:
            self._update_pasted_path_status(path_input.id, message, "red")
            return True

        path_input.value = normalized_path
        self._update_pasted_path_status(path_input.id, message, "green")
        if path_input.id == "path":
            self._autofill_logback_settings(normalized_path)
        return True

    def _update_pasted_path_status(self, field_id: str, message: str, color: str) -> None:
        if field_id == "path":
            self.query_one("#path-status", Static).update(
                format_path_status(message, color, require_writable=False)
            )
            return
        if field_id == "output_path":
            self.query_one("#output-path-status", Static).update(
                format_path_status(message, color, require_writable=True)
            )
            return
        self.query_one("#logback-xml-status", Static).update(f"[{color}]{message}[/{color}]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"output_path", "output_name"}:
            await self.action_confirm_export()
            return
        if event.input.id in {
            "path",
            "keyword",
            "log_pattern",
            "logback_xml_path",
            "start_date",
            "start_time",
            "end_date",
            "end_time",
        }:
            await self.action_run_analysis()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        async_actions = {
            "run": self.action_run_analysis,
            "confirm-export": self.action_confirm_export,
        }
        async_action = async_actions.get(button_id)
        if async_action is not None:
            await async_action()
            return

        actions = {
            "browse-events": self.action_browse_events,
            "export-result": self.action_show_export_settings,
            "clear": self.action_clear_result,
            "manage-recipes": self.action_manage_recipes,
            "toggle_advanced_settings": self.action_toggle_advanced_settings,
            "browse_path": self.action_browse_path,
            "browse_output_path": self.action_browse_output_path,
            "clear_keyword": self.action_clear_keyword,
            "browse_logback_xml": self.action_browse_logback_xml,
            "select_all_levels": partial(self._set_all_levels, True),
            "clear_levels": partial(self._set_all_levels, False),
        }
        action = actions.get(button_id)
        if action is not None:
            action()
            return
        await self._handle_zero_result_retry(button_id)

    async def _handle_zero_result_retry(self, button_id: str) -> None:
        retry_actions = {
            "retry_without_time": self.action_retry_without_time,
            "retry_without_level": self.action_retry_without_level,
            "retry_without_keyword": self.action_retry_without_keyword,
        }
        retry_action = retry_actions.get(button_id)
        if retry_action is not None:
            await retry_action()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "display_mode":
            last_result = getattr(self, "_last_result", None)
            if last_result is None:
                return

            result_box = self.query_one("#result-box", RichLog)
            self._set_result(
                result_box,
                build_dashboard_view(last_result, self._should_compact_dashboard(self.size.width, self.size.height)),
            )
            return

        if event.select.id != "pattern_mode":
            return

        self._sync_pattern_field_visibility()

    def update_path_preview(self, path: str, status_id: str = "#path-status", require_writable: bool = False) -> None:
        preview = self.query_one(status_id, Static)
        # 狀態列即時反映路徑可用性，避免要按執行才發現權限或拼字錯誤
        color, message, _, _ = inspect_directory_path(path, require_writable=require_writable)
        preview.update(format_path_status(message, color, require_writable=require_writable))

    def action_browse_path(self) -> None:
        self._open_directory_picker(
            target_id="path",
            title="選擇 Log 目錄",
            hint="可用樹狀瀏覽，或直接手動輸入路徑",
            confirm_label="使用此路徑",
            require_writable=False,
        )

    def action_browse_output_path(self) -> None:
        self._open_directory_picker(
            target_id="output_path",
            title="選擇目標資料夾",
            hint="可用樹狀瀏覽，或直接手動輸入路徑",
            confirm_label="使用此資料夾",
            require_writable=True,
        )

    def action_browse_logback_xml(self) -> None:
        current_path = self.query_one("#logback_xml_path", Input).value.strip() or ""
        self.push_screen(
            FilePickerScreen(
                current_path,
                "選擇 Logback XML",
                "請選擇 logback.xml 或 logback-spring.xml",
                "載入",
            ),
            callback=self._apply_and_load_logback_xml,
        )

    def _open_directory_picker(
        self,
        target_id: str,
        title: str,
        hint: str,
        confirm_label: str,
        require_writable: bool,
    ) -> None:
        current_path = self.query_one(f"#{target_id}", Input).value.strip() or "."
        # 瀏覽成功後直接回填到對應欄位，手動輸入仍保留為 fallback
        self.push_screen(
            DirectoryPickerScreen(current_path, title, hint, confirm_label, require_writable=require_writable),
            callback=lambda selected_path: self._apply_selected_directory(target_id, selected_path),
        )

    def _apply_selected_directory(self, target_id: str, selected_path: str | None) -> None:
        if not selected_path:
            return

        path_input = self.query_one(f"#{target_id}", Input)
        path_input.value = selected_path
        path_input.focus()
        self.update_path_preview(
            selected_path,
            "#output-path-status" if target_id == "output_path" else "#path-status",
            require_writable=target_id == "output_path",
        )
        if target_id == "path":
            self._autofill_logback_settings(selected_path)

    def _apply_selected_logback_xml(self, selected_path: str | None) -> None:
        if not selected_path:
            return

        xml_input = self.query_one("#logback_xml_path", Input)
        xml_input.value = selected_path
        xml_input.focus()

    def _apply_and_load_logback_xml(self, selected_path: str | None) -> None:
        self._apply_selected_logback_xml(selected_path)
        if selected_path:
            self.action_load_logback_xml()

    def _handle_unexpected_error(self, result_box: RichLog, exc: Exception) -> None:
        logger.exception("TUI 分析發生未預期錯誤", exc_info=exc)
        self._release_last_result()
        if not self.is_running:
            return
        self._set_result(result_box, build_error_view("執行失敗", str(exc)))
        self._set_result_status("failed")

    def _handle_analysis_error(self, result_box: RichLog, exc: Exception) -> None:
        self._release_last_result()
        if not self.is_running:
            return

        if isinstance(exc, NoMatchingLogsError):
            view = build_zero_result_view(exc.diagnostics)
            status = "no_results"
        elif isinstance(exc, PermissionError):
            view = build_error_view("權限不足", str(exc))
            status = "failed"
        elif isinstance(exc, FileNotFoundError):
            view = build_error_view("找不到資料夾", str(exc))
            status = "failed"
        else:
            title = "無可分析資料" if str(exc).startswith("找不到符合條件的 log") else "輸入錯誤"
            view = build_error_view(title, str(exc))
            status = "failed"

        self._set_result(result_box, view)
        self._set_result_status(status)

    def _save_recent_analysis_state(self, request: AnalysisRequest, display_mode: str) -> None:
        recent_state = load_recent_tui_state() or {}
        recent_state.update(
            {
                "path": request.path,
                "keyword": request.keyword or "",
                "ignore_case": request.ignore_case,
                "display_mode": display_mode,
                "sort_by": request.sort_by,
                "levels": list(request.levels or self._collect_checked_levels() or DEFAULT_SELECTED_LEVELS),
                "level_unrestricted": request.levels is None,
                "start_date": self.query_one("#start_date", Input).value.strip(),
                "start_time": self.query_one("#start_time", Input).value.strip(),
                "end_date": self.query_one("#end_date", Input).value.strip(),
                "end_time": self.query_one("#end_time", Input).value.strip(),
            }
        )
        self._save_recent_form_state(recent_state)

    async def _analyze_request(self, request: AnalysisRequest) -> AnalysisResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(analyze_logs, request))

    def _display_analysis_result(self, result_box: RichLog, result: AnalysisResult) -> None:
        self._last_result = result
        if not self.is_running:
            return
        self._set_result(
            result_box,
            build_dashboard_view(result, self._should_compact_dashboard(self.size.width, self.size.height)),
        )
        self._set_result_status("complete", result)

    async def action_run_analysis(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        if self._focused_inside_export_settings():
            if getattr(self.focused, "id", None) != "browse_output_path":
                await self.action_confirm_export()
            return

        run_button = self.query_one("#run", Button)
        result_box = self.query_one("#result-box", RichLog)
        self._operation_in_progress = True
        self._set_export_settings_visible(False)
        self.query_one("#export-status", Static).update("")
        self._release_last_result()

        # 記錄當下聚焦的元件，並暫時清除焦點，避免焦點自動轉移至「清除結果」按鈕造成反白
        original_focused = self.focused
        self.set_focus(None)

        # 停用按鈕以防二次點擊
        run_button.disabled = True
        self._set_result_status("running")
        self._set_result(result_box, build_loading_view())

        try:
            self._autofill_logback_settings(self.query_one("#path", Input).value.strip() or ".")
            request, display_mode = self._collect_form_values()
            self._save_recent_analysis_state(request, display_mode)
            result = await self._analyze_request(request)
            self._display_analysis_result(result_box, result)
        except (NoMatchingLogsError, PermissionError, FileNotFoundError, ValueError) as exc:
            self._handle_analysis_error(result_box, exc)
        except Exception as exc:  # noqa: BLE001
            self._handle_unexpected_error(result_box, exc)
        finally:
            self._operation_in_progress = False
            if self.is_running:
                run_button.disabled = False
                # 恢復原本的焦點元件
                if original_focused and original_focused.is_mounted and not original_focused.disabled:
                    self.set_focus(original_focused)
            if getattr(self, "_release_result_when_operation_finishes", False):
                self._release_result_when_operation_finishes = False
                self._release_last_result(close_browser=False)

    def action_show_export_settings(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        if self._last_result is None or self._result_state != "complete":
            return

        self._set_export_settings_visible(True)
        self.call_after_refresh(self._reveal_export_confirmation)

    def _reveal_export_confirmation(self) -> None:
        confirm_button = self.query_one("#confirm-export", Button)
        confirm_button.scroll_visible(animate=False)
        confirm_button.focus()

    def _set_export_controls_disabled(self, disabled: bool) -> None:
        for selector in ("#run", "#clear", "#export-result", "#confirm-export"):
            self.query_one(selector, Button).disabled = disabled

    def _capture_export_request(self) -> tuple[ResultExportOptions, bool, str]:
        selected_fields = tuple(
            field
            for field in EXPORT_FIELDS
            if self.query_one(f"#export_field_{field}", Checkbox).value
        )
        if not selected_fields:
            raise ValueError("請至少選擇一個輸出欄位")

        output_path = self.query_one("#output_path", Input).value.strip() or "."
        output_name = self.query_one("#output_name", Input).value
        fmt = str(self.query_one("#format", Select).value or "csv")
        split_by_level = self.query_one("#split_by_level", Checkbox).value
        include_manifest = self.query_one("#include_manifest", Checkbox).value
        recent_state = load_recent_tui_state() or {}
        recent_state.update(
            {
                "output_path": output_path,
                "format": fmt,
                "split_by_level": split_by_level,
            }
        )
        self._save_recent_form_state(recent_state)
        options = ResultExportOptions(
            output_path=build_output_path(output_path, output_name, fmt),
            fmt=fmt,
            fields=None if selected_fields == EXPORT_FIELDS else selected_fields,
            split_by_level=split_by_level,
        )
        return options, include_manifest, output_name

    def _display_export_success(
        self,
        result: AnalysisResult,
        export_outcome: tuple[list[str], str | None, Exception | None],
        output_name: str,
        export_status: Static,
    ) -> None:
        exported_files, manifest_path, manifest_error = export_outcome
        result_box = self.query_one("#result-box", RichLog)
        self._set_result(
            result_box,
            build_dashboard_view(
                result,
                self._should_compact_dashboard(self.size.width, self.size.height),
            ),
        )
        report_paths = ", ".join(exported_files) if exported_files else "完成"
        if manifest_error is not None:
            export_status.update(
                f"報表已匯出，證據清單失敗\n報表：{report_paths}\n錯誤：{manifest_error}"
            )
        elif manifest_path is not None:
            export_status.update(f"報表：{report_paths}\n證據清單：{manifest_path}")
        else:
            export_status.update(f"匯出完成：{report_paths}")
        if manifest_error is None:
            self._refresh_output_name_default(output_name)

    async def action_confirm_export(self) -> None:
        result = self._last_result
        if (
            getattr(self, "_operation_in_progress", False)
            or result is None
            or self._result_state != "complete"
        ):
            return

        export_status = self.query_one("#export-status", Static)
        self._operation_in_progress = True
        self._set_export_controls_disabled(True)
        export_status.update("匯出中…")

        try:
            options, include_manifest, output_name = self._capture_export_request()

            loop = asyncio.get_running_loop()
            exported_files, manifest_path, manifest_error = await loop.run_in_executor(
                None,
                partial(_export_reports_and_manifest, result, options, include_manifest),
            )
            if self.is_running:
                self._display_export_success(
                    result,
                    (exported_files, manifest_path, manifest_error),
                    output_name,
                    export_status,
                )
        except Exception as exc:  # noqa: BLE001 - export failures must preserve the analyzed result
            logger.warning("TUI export failed: %s", exc)
            if self.is_running:
                export_status.update(f"匯出失敗：{exc}")
        finally:
            self._operation_in_progress = False
            if self.is_running:
                self._set_export_controls_disabled(False)
            if getattr(self, "_release_result_when_operation_finishes", False):
                self._release_result_when_operation_finishes = False
                self._release_last_result(close_browser=False)

    async def action_retry_without_time(self) -> None:
        for field_id in ("start_date", "start_time", "end_date", "end_time"):
            self.query_one(f"#{field_id}", Input).value = ""
        await self.action_run_analysis()

    async def action_retry_without_level(self) -> None:
        self.query_one("#level_unrestricted", Checkbox).value = True
        await self.action_run_analysis()

    async def action_retry_without_keyword(self) -> None:
        self.query_one("#keyword", Input).value = ""
        await self.action_run_analysis()

    def action_clear_result(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        if self._last_result is None and self._result_state != "no_results":
            return
        result_box = self.query_one("#result-box", RichLog)
        self._release_last_result()
        self._set_export_settings_visible(False)
        self.query_one("#export-status", Static).update("")
        self._set_result(result_box, build_idle_view())
        self._set_result_status("idle")

    def action_browse_events(self) -> None:
        result = self._last_result
        if result is None:
            return
        self.push_screen(EventBrowserScreen(result))

    def _release_last_result(self, close_browser: bool = True) -> None:
        result = getattr(self, "_last_result", None)
        self._last_result = None
        if result is None:
            return

        try:
            active_screen = self.screen
        except Exception:  # noqa: BLE001 - app may be shutting down without an active screen
            active_screen = None
        if isinstance(active_screen, EventBrowserScreen) and active_screen.result is result:
            active_screen.invalidate()
            if close_browser and active_screen.is_mounted:
                active_screen.dismiss()

        matched_logs = getattr(result, "matched_logs", None)
        release = getattr(matched_logs, "release", None)
        if callable(release):
            try:
                release()
            except Exception:
                logger.exception("釋放分析結果暫存資料失敗")

    def action_clear_keyword(self) -> None:
        keyword_input = self.query_one("#keyword", Input)
        keyword_input.value = ""
        keyword_input.focus()

    def action_manage_recipes(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        self.push_screen(
            RecipeManagerScreen(RecipeStore(), self._capture_recipe_state, self._apply_recipe_state)
        )

    def _capture_recipe_state(self) -> dict[str, object]:
        state: dict[str, object] = {
            "path": self.query_one("#path", Input).value,
            "pattern_mode": str(self.query_one("#pattern_mode", Select).value or "default"),
            "log_pattern": self.query_one("#log_pattern", Input).value,
            "logback_xml_path": self.query_one("#logback_xml_path", Input).value,
            "start_date": self.query_one("#start_date", Input).value,
            "start_time": self.query_one("#start_time", Input).value,
            "end_date": self.query_one("#end_date", Input).value,
            "end_time": self.query_one("#end_time", Input).value,
            "keyword": self.query_one("#keyword", Input).value,
            "ignore_case": self.query_one("#ignore_case", Checkbox).value,
            "level_unrestricted": self.query_one("#level_unrestricted", Checkbox).value,
            "levels": list(self._collect_checked_levels()),
            "sort_by": str(self.query_one("#sort_by", Select).value or "time"),
            "display_mode": str(self.query_one("#display_mode", Select).value or "summary"),
        }
        return {field: state[field] for field in RECIPE_FIELDS}

    def _apply_recipe_state(self, name: str, recipe: Mapping[str, object]) -> str:
        self._auto_logback_xml_path = ""
        self._auto_log_pattern = ""
        self._last_logback_autofill_source = ""
        self._recipe_autofill_source = ""

        text_fields = {
            "path": "#path",
            "log_pattern": "#log_pattern",
            "logback_xml_path": "#logback_xml_path",
            "start_date": "#start_date",
            "start_time": "#start_time",
            "end_date": "#end_date",
            "end_time": "#end_time",
            "keyword": "#keyword",
        }
        for field, selector in text_fields.items():
            if field in recipe:
                self.query_one(selector, Input).value = str(recipe[field])

        if "path" in recipe:
            self._recipe_autofill_source = self._normalize_recipe_source(str(recipe["path"]))

        warnings = self._apply_recipe_enums(recipe)
        for field in ("ignore_case", "level_unrestricted"):
            if field in recipe and isinstance(recipe[field], bool):
                selector = "#ignore_case" if field == "ignore_case" else "#level_unrestricted"
                self.query_one(selector, Checkbox).value = recipe[field]
        self._sync_pattern_field_visibility()
        message = f"已載入配方「{name}」的條件；尚未執行分析。"
        if warnings:
            message = f"{message} 已忽略不支援的欄位值：{', '.join(warnings)}。"
        feedback = self.query_one("#recipe-feedback", Static)
        feedback.update(message)
        feedback.display = True
        return message

    def _apply_recipe_enums(self, recipe: Mapping[str, object]) -> list[str]:
        warnings: list[str] = []
        select_fields = {
            "pattern_mode": ("#pattern_mode", {"default", "custom"}),
            "sort_by": ("#sort_by", {"time", "level"}),
            "display_mode": ("#display_mode", {"full", "summary"}),
        }
        for field, (selector, supported_values) in select_fields.items():
            if field not in recipe:
                continue
            value = recipe[field]
            if value in supported_values:
                self.query_one(selector, Select).value = value
            else:
                warnings.append(field)

        if "levels" in recipe and isinstance(recipe["levels"], list):
            allowed_levels = {"ERROR", "WARN", "INFO", "DEBUG", "TRACE"}
            levels = {str(level).upper() for level in recipe["levels"]}
            if levels - allowed_levels:
                warnings.append("levels")
            for level in allowed_levels:
                self.query_one(f"#level_{level}", Checkbox).value = level in levels
        return warnings

    @staticmethod
    def _normalize_recipe_source(path: str) -> str:
        return os.path.abspath(os.path.expanduser(path.strip() or "."))

    def action_toggle_advanced_settings(self) -> None:
        self._set_advanced_settings_visible(not self._advanced_settings_visible)

    def _set_advanced_settings_visible(self, visible: bool) -> None:
        self._advanced_settings_visible = visible
        advanced_settings = self.query_one("#advanced-settings", Container)
        advanced_settings.display = visible

        toggle_button = self.query_one("#toggle_advanced_settings", Button)
        toggle_button.label = "收合進階設定" if visible else "展開進階設定"

    def _set_export_settings_visible(self, visible: bool) -> None:
        self.query_one("#export-settings", Container).display = visible

    def _focused_inside_export_settings(self) -> bool:
        widget = self.focused
        while widget is not None:
            if widget.id == "export-settings":
                return True
            widget = widget.parent
        return False

    def action_load_logback_xml(self) -> None:
        status = self.query_one("#logback-xml-status", Static)
        xml_path = self.query_one("#logback_xml_path", Input).value.strip()
        if not xml_path:
            status.update("請輸入 logback.xml / logback-spring.xml 路徑")
            return
        if not os.path.isfile(xml_path):
            status.update(f"找不到檔案：{os.path.abspath(xml_path)}")
            return

        log_dir = self.query_one("#path", Input).value.strip() or "."
        best_pattern = find_best_logback_pattern(xml_path, log_dir)
        if best_pattern is None:
            status.update("找不到可用的 Logback pattern")
            return

        self.query_one("#pattern_mode", Select).value = "custom"
        pattern_input = self.query_one("#log_pattern", Input)
        pattern_input.value = best_pattern.pattern
        pattern_input.focus()
        self._sync_pattern_field_visibility()
        status.update(
            f"已載入 {best_pattern.name}，命中 {best_pattern.matches}/{best_pattern.checked}"
        )

    def _autofill_logback_settings(self, log_dir: str) -> None:
        normalized_dir = self._normalize_recipe_source(log_dir)
        if normalized_dir == getattr(self, "_recipe_autofill_source", ""):
            return
        if not os.path.isdir(normalized_dir):
            return

        xml_input = self.query_one("#logback_xml_path", Input)
        pattern_input = self.query_one("#log_pattern", Input)
        current_xml = xml_input.value.strip()
        current_pattern = pattern_input.value.strip()
        can_update_xml = not current_xml or current_xml == self._auto_logback_xml_path
        can_update_pattern = not current_pattern or current_pattern == self._auto_log_pattern

        if (
            normalized_dir == self._last_logback_autofill_source
            and current_xml == self._auto_logback_xml_path
        ):
            return

        if not can_update_xml:
            return

        best_xml_path = ""
        best_pattern = None
        for xml_path in self._find_logback_xml_candidates(normalized_dir):
            candidate = find_best_logback_pattern(xml_path, normalized_dir)
            if candidate is None:
                continue
            if best_pattern is None or (candidate.matches, candidate.score) > (best_pattern.matches, best_pattern.score):
                best_xml_path = xml_path
                best_pattern = candidate

        if best_pattern is None:
            return

        if can_update_xml:
            xml_input.value = best_xml_path
            self._auto_logback_xml_path = best_xml_path

        if can_update_pattern:
            pattern_input.value = best_pattern.pattern
            self._auto_log_pattern = best_pattern.pattern
            self.query_one("#pattern_mode", Select).value = "custom"
            self._sync_pattern_field_visibility()

        self.query_one("#logback-xml-status", Static).update(
            f"已自動載入 {Path(best_xml_path).name}，命中 {best_pattern.matches}/{best_pattern.checked}"
        )
        self._last_logback_autofill_source = normalized_dir

    def _find_logback_xml_candidates(self, log_dir: str) -> list[str]:
        candidates: list[str] = []
        try:
            filenames = sorted(os.listdir(log_dir))
        except OSError:
            return candidates

        for filename in filenames:
            lower = filename.lower()
            if not lower.startswith("logback") or not lower.endswith(".xml"):
                continue

            candidate_path = os.path.join(log_dir, filename)
            if os.path.isfile(candidate_path):
                candidates.append(candidate_path)

        return candidates

    def _refresh_output_name_default(self, previous_output_name: str) -> None:
        previous_clean = previous_output_name.strip()
        if previous_clean not in {"", getattr(self, "_auto_output_name", "")}:
            return

        self._auto_output_name = get_default_output_name_text()
        output_name_input = self.query_one("#output_name", Input)
        output_name_input.value = self._auto_output_name

    def _collect_form_values(self) -> tuple[AnalysisRequest, str]:
        path = self.query_one("#path", Input).value.strip() or "."
        start_date = self.query_one("#start_date", Input).value.strip()
        start_time = self.query_one("#start_time", Input).value.strip()
        end_date = self.query_one("#end_date", Input).value.strip()
        end_time = self.query_one("#end_time", Input).value.strip()
        keyword = self.query_one("#keyword", Input).value.strip()
        pattern_mode = self.query_one("#pattern_mode", Select).value or "default"
        log_pattern = self.query_one("#log_pattern", Input).value.strip()
        display_mode = self.query_one("#display_mode", Select).value or "summary"
        sort_by = self.query_one("#sort_by", Select).value or "time"
        selected_levels = self._collect_selected_levels()
        ignore_case = self.query_one("#ignore_case", Checkbox).value
        start_dt, end_dt = parse_datetime_range_inputs(
            start_date,
            start_time,
            end_date,
            end_time,
        )
        request = AnalysisRequest(
            path=path,
            start_dt=start_dt,
            end_dt=end_dt,
            keyword=keyword or None,
            ignore_case=ignore_case,
            sort_by=str(sort_by),
            log_pattern=log_pattern if pattern_mode == "custom" else None,
            levels=selected_levels,
        )
        return request, str(display_mode)

    def _collect_checked_levels(self) -> tuple[str, ...]:
        return tuple(
            level
            for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")
            if self.query_one(f"#level_{level}", Checkbox).value
        )

    def _collect_selected_levels(self) -> tuple[str, ...] | None:
        if self.query_one("#level_unrestricted", Checkbox).value:
            return None

        levels = self._collect_checked_levels()
        if not levels:
            raise ValueError("至少選擇一個 Log 層級")
        return levels

    def _set_all_levels(self, selected: bool) -> None:
        for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
            self.query_one(f"#level_{level}", Checkbox).value = selected

    def _should_compact_dashboard(self, width: int, height: int) -> bool:
        if self._is_compact(width, height):
            return True
        display_mode = self.query_one("#display_mode", Select).value or "summary"
        return str(display_mode) != "full"

    def _set_result(self, result_box: RichLog, renderable: object) -> None:
        result_box.clear()
        content_region = getattr(result_box, "scrollable_content_region", None)
        width = getattr(content_region, "width", None)
        result_box.write(renderable, width=width if width and width > 0 else None, expand=True)
        result_box.scroll_home()

    def _set_result_status(self, state: str, result: AnalysisResult | None = None) -> None:
        status = self.query_one("#result-status", Static)
        clear_button = self.query_one("#clear", Button)
        export_button = self.query_one("#export-result", Button)
        confirm_button = self.query_one("#confirm-export", Button)
        self._result_state = state
        can_export = state == "complete" and result is not None
        export_button.disabled = not can_export
        confirm_button.disabled = not can_export
        retry_actions = self.query_one("#zero-result-actions", Container)
        if state == "no_results":
            retry_actions.remove_class("zero-results-hidden")
        else:
            retry_actions.add_class("zero-results-hidden")

        if state == "complete" and result is not None:
            status.update(f"狀態：完成｜總 Log {result.total_logs}｜命中 {result.matched_occurrences}")
            clear_button.disabled = False
            self.query_one("#browse-events", Button).disabled = False
            for button_id in ("retry_without_time", "retry_without_level", "retry_without_keyword"):
                self.query_one(f"#{button_id}", Button).disabled = True
            return

        if state == "no_results":
            status.update("狀態：沒有符合資料")
            clear_button.disabled = False
            self.query_one("#browse-events", Button).disabled = True
            has_time_filter = any(
                self.query_one(f"#{field_id}", Input).value.strip()
                for field_id in ("start_date", "start_time", "end_date", "end_time")
            )
            has_level_filter = not self.query_one("#level_unrestricted", Checkbox).value
            has_keyword_filter = bool(self.query_one("#keyword", Input).value.strip())
            self.query_one("#retry_without_time", Button).disabled = not has_time_filter
            self.query_one("#retry_without_level", Button).disabled = not has_level_filter
            self.query_one("#retry_without_keyword", Button).disabled = not has_keyword_filter
            return

        messages = {
            "idle": "狀態：尚未分析",
            "running": "狀態：分析中",
            "failed": "狀態：失敗",
        }
        status.update(messages[state])
        clear_button.disabled = True
        self.query_one("#browse-events", Button).disabled = True
        for button_id in ("retry_without_time", "retry_without_level", "retry_without_keyword"):
            self.query_one(f"#{button_id}", Button).disabled = True

    def _sync_pattern_field_visibility(self) -> None:
        pattern_mode = self.query_one("#pattern_mode", Select).value or "default"
        try:
            pattern_field = self.query_one("#pattern-field", Container)
        except (KeyError, LookupError):
            return
        pattern_field.display = pattern_mode == "custom"

    def _restore_recent_form_state(self) -> None:
        state = load_recent_tui_state()
        if not state:
            return

        for selector in ("#path", "#output_path", "#keyword", "#start_date", "#start_time", "#end_date", "#end_time"):
            value = state.get(selector.lstrip("#"))
            if value is not None:
                self.query_one(selector, Input).value = str(value)

        if isinstance(state.get("ignore_case"), bool):
            self.query_one("#ignore_case", Checkbox).value = state["ignore_case"]
        if state.get("display_mode") in {"full", "summary"}:
            self.query_one("#display_mode", Select).value = str(state["display_mode"])
        if state.get("sort_by") in {"time", "level"}:
            self.query_one("#sort_by", Select).value = str(state["sort_by"])
        if state.get("format") in {"csv", "json", "md"}:
            self.query_one("#format", Select).value = str(state["format"])
        if isinstance(state.get("split_by_level"), bool):
            self.query_one("#split_by_level", Checkbox).value = state["split_by_level"]
        if isinstance(state.get("level_unrestricted"), bool):
            self.query_one("#level_unrestricted", Checkbox).value = state["level_unrestricted"]
        levels = state.get("levels")
        if isinstance(levels, list):
            selected_levels = {str(level).upper() for level in levels}
            for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
                self.query_one(f"#level_{level}", Checkbox).value = level in selected_levels

    def _save_recent_form_state(self, state: dict[str, object]) -> None:
        save_recent_tui_state(state)


if __name__ == "__main__":
    app = LogAnalyzerApp()
    app.run()
