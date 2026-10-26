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
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    RichLog,
    Select,
    Static,
)
from log_analyzer.application.analysis_service import (
    DEFAULT_SELECTED_LEVELS,
    AnalysisRequest,
    AnalysisResult,
    analyze_logs,
)
from log_analyzer.infrastructure.recipe_store import RECIPE_FIELDS, RecipeStore
from log_analyzer.presentation.tui.screens.recipe_manager import (
    RecipeManagerScreen,
    RecipeTrackingContext,
)
from log_analyzer.presentation.tui.recent_state import (
    load_recent_tui_state,
    save_recent_tui_state,
)
from log_analyzer.presentation.tui.form_values import (
    get_default_output_name_text,
    parse_datetime_range_inputs,
)
from log_analyzer.presentation.tui.controllers.sources import SourceControllerMixin
from log_analyzer.presentation.tui.controllers.conditions import ConditionsControllerMixin
from log_analyzer.presentation.tui.controllers.patterns import PatternSetupMixin
from log_analyzer.presentation.tui.controllers.analysis import AnalysisLifecycleMixin
from log_analyzer.presentation.tui.controllers.exporting import ExportWorkflowMixin
from log_analyzer.presentation.tui.controllers.results import ResultStateMixin
from log_analyzer.presentation.tui.composition import ViewCompositionMixin
from log_analyzer.presentation.tui.controllers.analysis import _PendingPatternAnalysis
from log_analyzer.infrastructure.paths import get_system_root_path


_RECIPE_INPUT_IDS = frozenset(
    {
        "path",
        "log_pattern",
        "logback_xml_path",
        "start_date",
        "start_time",
        "end_date",
        "end_time",
        "keyword",
    }
)
_RECIPE_CHECKBOX_IDS = frozenset(
    {
        "manual_pattern_override",
        "ignore_case",
        "level_unrestricted",
        "level_ERROR",
        "level_WARN",
        "level_INFO",
        "level_DEBUG",
        "level_TRACE",
    }
)


logger = logging.getLogger(__name__)
__all__ = ["get_system_root_path"]


class LogAnalyzerApp(
    ConditionsControllerMixin,
    SourceControllerMixin,
    PatternSetupMixin,
    AnalysisLifecycleMixin,
    ExportWorkflowMixin,
    ResultStateMixin,
    ViewCompositionMixin,
    App,
):
    TITLE = "Java Log Analyzer"
    _PACKAGE_ROOT = Path(__file__).resolve().parents[2]
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
    _auto_logback_xml_paths: tuple[str, ...] = ()
    _last_logback_autofill_source: str = ""
    _pending_pattern_analysis: _PendingPatternAnalysis | None = None
    BINDINGS = [
        ("q", "quit", "離開"),
        ("enter", "schedule_analysis", "開始分析"),
        ("ctrl+x", "cancel_analysis", "取消分析"),
        ("c", "clear_result", "清除結果"),
        ("a", "toggle_advanced_settings", "進階設定"),
        ("d", "browse_summary", "摘要導覽"),
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

    def on_mount(self) -> None:
        self._auto_logback_xml_paths = ()
        self._pending_pattern_analysis = None
        self._restored_encoding_warning = None
        path_input = self.query_one("#path", Input)
        output_path_input = self.query_one("#output_path", Input)
        self.query_one("#result-box", RichLog).can_focus = False
        self._restore_recent_form_state()
        self._sync_pattern_field_visibility()
        self._sync_pattern_preview_for_current_selection()
        self._sync_level_controls()
        self.call_after_refresh(self.set_focus, None)
        self._set_advanced_settings_visible(False)
        self._set_export_settings_visible(False)
        self._sync_layout(self.size.width, self.size.height)
        self._set_result_status("idle")
        self._set_analysis_cancel_button(False)
        self.update_path_preview(path_input.value)
        self.update_path_preview(
            output_path_input.value, "#output-path-status", require_writable=True
        )

    def on_unmount(self) -> None:
        control = getattr(self, "_analysis_control", None)
        if control is not None:
            control.cancel()
        self._active_analysis_operation_id = None
        self._analysis_control = None
        if getattr(self, "_pending_pattern_analysis", None) is not None:
            self._pending_pattern_analysis = None
            self._operation_in_progress = False
            self._release_last_result(close_browser=False)
        if getattr(self, "_pending_export_conflict", None) is not None:
            self._pending_export_conflict = None
            self._operation_in_progress = False
        if getattr(self, "_operation_in_progress", False):
            self._release_result_when_operation_finishes = True
        else:
            self._release_last_result(close_browser=False)

    def on_resize(self, event: Resize) -> None:
        self._sync_layout(event.size.width, event.size.height)

        if getattr(self, "_last_result", None) is None:
            self.update_path_preview(self.query_one("#path", Input).value)

    def _sync_layout(self, width: int, height: int) -> None:
        if height <= 0:
            return

        compact = self._is_compact(width, height)
        form_panel = self.query_one("#form-panel", ScrollableContainer)
        form_actions = self.query_one("#form-actions", Container)
        content = self.query_one("#content", ScrollableContainer)
        shell = self.query_one("#shell", Container)
        hero = self.query_one("#hero", Container)
        hero_title_row = self.query_one("#hero-title-row", Container)
        shell.set_class(height <= 32, "short")

        if compact:
            shell.remove_class("wide")
            shell.add_class("compact")
            hero.remove_class("wide")
            hero.add_class("compact")
            content.remove_class("wide")
            content.add_class("compact")
            form_panel.remove_class("wide")
            form_panel.add_class("compact")
            form_actions.remove_class("wide")
            form_actions.add_class("compact")
            hero_title_row.remove_class("wide")
            hero_title_row.add_class("compact")
        else:
            shell.remove_class("compact")
            shell.add_class("wide")
            hero.remove_class("compact")
            hero.add_class("wide")
            content.remove_class("compact")
            content.add_class("wide")
            form_panel.remove_class("compact")
            form_panel.add_class("wide")
            form_actions.remove_class("compact")
            form_actions.add_class("wide")
            hero_title_row.remove_class("compact")
            hero_title_row.add_class("wide")

        if height <= 32:
            content.add_class("short")
        else:
            content.remove_class("short")

        self.call_after_refresh(self._sync_advanced_layout)
        self.call_after_refresh(self._sync_advanced_layout_after_refresh)

    def _sync_advanced_layout(self) -> None:
        if not self.is_mounted:
            return

        advanced_settings = self.query_one("#advanced-settings", Container)
        is_wide = advanced_settings.content_size.width >= 72
        was_wide = advanced_settings.has_class("advanced-wide")
        was_compact = advanced_settings.has_class("advanced-compact")
        if is_wide and not was_wide:
            advanced_settings.remove_class("advanced-compact")
            advanced_settings.add_class("advanced-wide")
        elif not is_wide and not was_compact:
            advanced_settings.remove_class("advanced-wide")
            advanced_settings.add_class("advanced-compact")
        if is_wide != was_wide or (not is_wide) != was_compact:
            self.call_after_refresh(self._sync_advanced_layout)

    def _sync_advanced_layout_after_refresh(self) -> None:
        if self.is_mounted:
            self.call_after_refresh(self._sync_advanced_layout)

    def _is_compact(self, width: int, height: int) -> bool:
        return width < 112 or height < 18

    def on_input_changed(self, event: Input.Changed) -> None:
        if not self.is_running:
            return
        if event.input.id in _RECIPE_INPUT_IDS:
            self._schedule_recipe_feedback_refresh()
        self.call_after_refresh(self._sync_result_context_notice)
        self.call_after_refresh(self._refresh_condition_overview)
        if event.input.id == "path":
            normalized_path = self._normalize_recipe_source(event.value)
            if normalized_path != getattr(self, "_recipe_autofill_source", ""):
                self._recipe_autofill_source = ""
            if (
                normalized_path != getattr(self, "_last_logback_autofill_source", "")
                and self.query_one("#logback_xml_path", Input).value.strip()
                == getattr(self, "_auto_logback_xml_path", "")
                and self._auto_logback_xml_path
            ):
                self.query_one("#logback_xml_path", Input).value = ""
                self._auto_logback_xml_path = ""
                self._auto_logback_xml_paths = ()
                self._last_logback_autofill_source = ""
            self._sync_pattern_preview_for_current_selection()
            self.update_path_preview(event.value)
        elif event.input.id == "output_path":
            self.update_path_preview(
                event.value, "#output-path-status", require_writable=True
            )
        elif event.input.id == "logback_xml_path":
            current_xml_path = self.query_one("#logback_xml_path", Input).value.strip()
            if current_xml_path and current_xml_path != getattr(
                self, "_auto_logback_xml_path", ""
            ):
                self._recipe_autofill_source = ""
            if current_xml_path != getattr(self, "_auto_logback_xml_path", ""):
                self._auto_logback_xml_paths = ()
            if current_xml_path != getattr(self, "_auto_logback_xml_path", ""):
                self._sync_pattern_preview_for_current_selection()
        elif event.input.id == "log_pattern":
            if self.query_one("#manual_pattern_override", Checkbox).value:
                self._sync_pattern_preview_for_current_selection()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id in _RECIPE_CHECKBOX_IDS:
            self._schedule_recipe_feedback_refresh()
        self.call_after_refresh(self._sync_result_context_notice)
        self.call_after_refresh(self._refresh_condition_overview)
        if event.checkbox.id == "manual_pattern_override":
            pattern_input = self.query_one("#log_pattern", Input)
            if event.value and not pattern_input.value.strip():
                seed_pattern = getattr(self, "_preview_seed_pattern", None)
                if seed_pattern:
                    pattern_input.value = seed_pattern
            self._sync_pattern_field_visibility()
            self._sync_pattern_preview_for_current_selection()
        elif event.checkbox.id == "level_unrestricted":
            self._sync_level_controls()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sort_by":
            self._schedule_recipe_feedback_refresh()
            self.call_after_refresh(self._sync_result_context_notice)

    def on_paste(self, event: Paste) -> None:
        """只在路徑欄位接收終端拖放所產生的貼上內容。"""
        focused = self.focused
        if not isinstance(focused, Input) or not self.handle_path_paste(
            focused, event.text
        ):
            return
        event.stop()

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
            self._schedule_analysis_start()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "run":
            self._schedule_analysis_start()
            return
        if button_id == "confirm-export":
            await self.action_confirm_export()
            return

        actions = {
            "preview-format": self.action_preview_format,
            "cancel-analysis": self.action_cancel_analysis,
            "browse-events": self.action_browse_events,
            "export-result": self.action_show_export_settings,
            "clear": self.action_clear_result,
            "manage-recipes": self.action_manage_recipes,
            "toggle_advanced_settings": self.action_toggle_advanced_settings,
            "browse_path": self.action_browse_path,
            "select-source-files": self.action_select_source_files,
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

    def _save_recent_analysis_state(
        self, request: AnalysisRequest, display_mode: str
    ) -> None:
        recent_state = load_recent_tui_state() or {}
        recent_state.update(
            {
                "path": request.path,
                "keyword": request.keyword or "",
                "ignore_case": request.ignore_case,
                # Keep older recent-state files stable while the dashboard adapts to terminal size.
                "display_mode": "full",
                "sort_by": request.sort_by,
                "levels": list(
                    request.levels
                    or self._collect_checked_levels()
                    or DEFAULT_SELECTED_LEVELS
                ),
                "level_unrestricted": request.levels is None,
                "start_date": self.query_one("#start_date", Input).value.strip(),
                "start_time": self.query_one("#start_time", Input).value.strip(),
                "end_date": self.query_one("#end_date", Input).value.strip(),
                "end_time": self.query_one("#end_time", Input).value.strip(),
            }
        )
        self._save_recent_form_state(recent_state)

    async def _analyze_request(self, request: AnalysisRequest) -> AnalysisResult:
        """將同步的 analyze_logs 放到執行緒池，等待結果時讓 UI 仍能處理事件。

        async 本身不會把解析變成背景執行緒；run_in_executor 才負責轉交工作。
        partial 預先綁定請求與 control，建立供執行緒呼叫的無參數函式。
        await 等待其結果或例外；取消仍須由 control 的檢查點合作完成。

        Args:
            request: 本輪固定的 AnalysisRequest，包含來源與篩選條件。

        Returns:
            AnalysisResult: 執行緒池中分析完成後的結果。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
            AnalysisCancelled: control 觀察到取消要求時停止處理。
            ValueError: 來源範圍無效或 Pattern 不受支援時停止處理。
            NoMatchingLogsError: 沒有符合條件的事件。
        """
        loop = asyncio.get_running_loop()
        control = getattr(self, "_analysis_control", None)
        return await loop.run_in_executor(
            None, partial(analyze_logs, request, control=control)
        )

    def action_clear_keyword(self) -> None:
        keyword_input = self.query_one("#keyword", Input)
        keyword_input.value = ""
        keyword_input.focus()

    def action_manage_recipes(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        self.push_screen(
            RecipeManagerScreen(
                RecipeStore(),
                self._capture_recipe_state,
                self._apply_recipe_state,
                tracking=RecipeTrackingContext(
                    active_recipe_name=getattr(self, "_active_recipe_name", None),
                    on_saved=self._on_recipe_saved,
                    on_deleted=self._on_recipe_deleted,
                ),
            )
        )

    def _capture_recipe_state(self) -> dict[str, object]:
        state: dict[str, object] = {
            "path": self.query_one("#path", Input).value,
            "pattern_mode": "custom"
            if self.query_one("#manual_pattern_override", Checkbox).value
            else "auto",
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
            # Preserve the legacy recipe field for existing recipe files and callers.
            "display_mode": "full",
        }
        return {field: state[field] for field in RECIPE_FIELDS}

    def _apply_recipe_state(self, name: str, recipe: Mapping[str, object]) -> str:
        self._auto_logback_xml_path = ""
        self._auto_logback_xml_paths = ()
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

        warnings = self._apply_recipe_enums(recipe)
        for field in ("ignore_case", "level_unrestricted"):
            if field in recipe and isinstance(recipe[field], bool):
                selector = (
                    "#ignore_case" if field == "ignore_case" else "#level_unrestricted"
                )
                self.query_one(selector, Checkbox).value = recipe[field]

        default_fields_cleared = False
        if "pattern_mode" in recipe:
            pattern_mode = recipe["pattern_mode"]
            override = self.query_one("#manual_pattern_override", Checkbox)
            if pattern_mode == "auto":
                override.value = False
            elif pattern_mode == "custom":
                override.value = bool(
                    self.query_one("#log_pattern", Input).value.strip()
                )
            elif pattern_mode == "default":
                pattern_input = self.query_one("#log_pattern", Input)
                xml_input = self.query_one("#logback_xml_path", Input)
                default_fields_cleared = bool(
                    pattern_input.value.strip() or xml_input.value.strip()
                )
                pattern_input.value = ""
                xml_input.value = ""
                override.value = False
                self._auto_logback_xml_paths = ()
                default_source = str(
                    recipe.get("path") or self.query_one("#path", Input).value or "."
                )
                self._recipe_autofill_source = self._normalize_recipe_source(
                    default_source
                )
            else:
                warnings.append("pattern_mode")

        self._sync_pattern_field_visibility()
        self._sync_pattern_preview_for_current_selection()
        self._sync_level_controls()
        message = f"已載入配方「{name}」的條件；尚未執行分析。"
        if default_fields_cleared:
            message = f"{message} 舊版預設模式中未啟用的 XML 與 Pattern 已清除。"
        if warnings:
            message = f"{message} 已忽略不支援的欄位值：{', '.join(warnings)}。"
        self._recipe_feedback_message = message
        self._track_recipe_snapshot(name)
        self._refresh_recipe_feedback()
        return message

    def _schedule_recipe_feedback_refresh(self) -> None:
        """在表單變更後安排配方差異更新。

        Returns:
            None: 安排一次刷新；目前沒有追蹤的配方時不執行。
        """
        if getattr(self, "_active_recipe_name", None):
            self.call_after_refresh(self._refresh_recipe_feedback)

    def _track_recipe_snapshot(
        self, name: str, state: Mapping[str, object] | None = None
    ) -> None:
        """將已載入或成功儲存的配方條件設為比較基準。

        Args:
            name: 配方名稱。
            state: 成功儲存的條件；省略時擷取目前表單。

        Returns:
            None: 更新目前配方名稱與條件快照。
        """
        current = state if state is not None else self._capture_recipe_state()
        self._active_recipe_name = name
        self._active_recipe_snapshot = {
            field: list(current[field]) if field == "levels" else current[field]
            for field in RECIPE_FIELDS
            if field in current
        }

    def _refresh_recipe_feedback(self) -> None:
        """依照目前表單與已追蹤快照更新主畫面配方提示。

        Returns:
            None: 顯示配方名稱與表單是否已修改。
        """
        feedback = self.query_one("#recipe-feedback", Static)
        message = getattr(self, "_recipe_feedback_message", "")
        name = getattr(self, "_active_recipe_name", None)
        snapshot = getattr(self, "_active_recipe_snapshot", None)
        if name and snapshot is not None:
            current = self._capture_recipe_state()
            status = f"目前配方：「{name}」"
            if current != snapshot:
                status += " · 已修改"
            message = f"{message}\n{status}" if message else status
        feedback.update(message)
        feedback.display = bool(message)

    def _on_recipe_saved(
        self, name: str, state: Mapping[str, object]
    ) -> None:
        """在配方成功儲存後更新追蹤基準。

        Args:
            name: 已儲存的配方名稱。
            state: 實際寫入儲存區的條件。

        Returns:
            None: 將目前表單標記為已儲存狀態。
        """
        self._recipe_feedback_message = f"已儲存配方「{name}」。"
        self._track_recipe_snapshot(name, state)
        self._refresh_recipe_feedback()

    def _on_recipe_deleted(self, name: str) -> None:
        """解除已刪除配方的追蹤，避免提示仍指向已不存在的檔案。

        Args:
            name: 已刪除的配方名稱。

        Returns:
            None: 只在刪除目前追蹤配方時清除其快照。
        """
        if getattr(self, "_active_recipe_name", None) != name:
            return
        self._active_recipe_name = None
        self._active_recipe_snapshot = None
        self._recipe_feedback_message = (
            f"配方「{name}」已刪除；目前表單條件尚未儲存。"
        )
        self._refresh_recipe_feedback()

    def _apply_recipe_enums(self, recipe: Mapping[str, object]) -> list[str]:
        warnings: list[str] = []
        select_fields = {
            "sort_by": ("#sort_by", {"time", "level"}),
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
        if self.is_mounted:
            self._sync_advanced_layout()
            self.call_after_refresh(self._sync_advanced_layout)
            self.call_after_refresh(self._sync_advanced_layout_after_refresh)

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
        log_pattern = self.query_one("#log_pattern", Input).value.strip()
        # Keep the second return value for callers that still expect the legacy tuple shape.
        display_mode = "full"
        sort_by = self.query_one("#sort_by", Select).value or "time"
        selected_levels = self._collect_selected_levels()
        ignore_case = self.query_one("#ignore_case", Checkbox).value
        if not self.query_one("#manual_pattern_override", Checkbox).value:
            log_pattern = getattr(self, "_analysis_log_pattern", None)
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
            log_pattern=log_pattern or None,
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
        if self.query_one("#level_unrestricted", Checkbox).value:
            return
        for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
            self.query_one(f"#level_{level}", Checkbox).value = selected

    def _sync_level_controls(self) -> None:
        unrestricted = self.query_one("#level_unrestricted", Checkbox).value
        for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
            self.query_one(f"#level_{level}", Checkbox).disabled = unrestricted
        self.query_one("#select_all_levels", Button).disabled = unrestricted
        self.query_one("#clear_levels", Button).disabled = unrestricted

    def _restore_recent_form_state(self) -> None:
        state = load_recent_tui_state()
        if not state:
            return

        for selector in (
            "#path",
            "#output_path",
            "#keyword",
            "#start_date",
            "#start_time",
            "#end_date",
            "#end_time",
        ):
            value = state.get(selector.lstrip("#"))
            if value is not None:
                self.query_one(selector, Input).value = str(value)

        if isinstance(state.get("ignore_case"), bool):
            self.query_one("#ignore_case", Checkbox).value = state["ignore_case"]
        if state.get("sort_by") in {"time", "level"}:
            self.query_one("#sort_by", Select).value = str(state["sort_by"])
        if state.get("format") in {"csv", "json", "md"}:
            self.query_one("#format", Select).value = str(state["format"])
        if isinstance(state.get("split_by_level"), bool):
            self.query_one("#split_by_level", Checkbox).value = state["split_by_level"]
        if isinstance(state.get("level_unrestricted"), bool):
            self.query_one("#level_unrestricted", Checkbox).value = state[
                "level_unrestricted"
            ]
        levels = state.get("levels")
        if isinstance(levels, list):
            selected_levels = {str(level).upper() for level in levels}
            for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
                self.query_one(f"#level_{level}", Checkbox).value = (
                    level in selected_levels
                )

    def _save_recent_form_state(self, state: dict[str, object]) -> None:
        save_recent_tui_state(state)
