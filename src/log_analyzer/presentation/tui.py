from __future__ import annotations

import asyncio
import os
from functools import partial
from pathlib import Path
from typing import Optional, Tuple

from textual.app import App, ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.events import Resize
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RichLog, Select, Static

from ..application.analysis_service import AnalysisResult, build_output_path, run_analysis
from ..domain.logback_pattern import DEFAULT_LOGBACK_PATTERN
from ..domain.logback_xml import find_best_logback_pattern
from .tui_dialogs import DirectoryPickerScreen, FilePickerScreen, ShortcutInput
from .recent_form_state import load_recent_tui_state, save_recent_tui_state
from .tui_inputs import (
    get_default_end_date_text,
    get_default_end_time_text,
    get_default_output_name_text,
    get_default_start_date_text,
    get_default_start_time_text,
    parse_datetime_range_inputs,
)
from .tui_views import build_dashboard_view, build_error_view, build_idle_view, build_loading_view, format_path_status
from ..infrastructure.paths import get_system_root_path, inspect_directory_path
from ..version import get_package_version


class LogAnalyzerApp(App):
    TITLE = "Java Log Analyzer"
    _PACKAGE_ROOT = Path(__file__).resolve().parents[1]
    CSS_PATH = [
        str(_PACKAGE_ROOT / "tcss" / "tui.base.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.layout.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.dialog.tcss"),
        str(_PACKAGE_ROOT / "tcss" / "tui.responsive.tcss"),
    ]
    _last_result: Optional[AnalysisResult] = None
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
            with Container(id="hero"):
                with Container(id="hero-title-row"):
                    yield Static("Java Log Analyzer", id="hero-title")
                    yield Static(f"v{get_package_version()}", id="hero-version")
                    yield Static("·", id="hero-separator")
                    yield Static("分析、篩選並匯出 Java Logback 記錄", id="hero-subtitle")

            with ScrollableContainer(id="content", can_focus=False):
                with ScrollableContainer(id="form-panel", can_focus=False):
                    yield Static("輸入設定", classes="section-title")

                    with Container(classes="field"):
                        yield Label("Log 目錄")
                        with Container(classes="path-row"):
                            yield ShortcutInput(placeholder="例如：./logs", value=".", id="path")
                            yield Button("瀏覽", id="browse_path")
                    yield Static("", classes="path-status", id="path-status")

                    with Container(classes="field"):
                        yield Label("目標資料夾")
                        with Container(classes="path-row"):
                            yield ShortcutInput(placeholder="例如：./exports", value=".", id="output_path")
                            yield Button("瀏覽", id="browse_output_path")
                    yield Static("", classes="path-status", id="output-path-status")

                    with Container(classes="field"):
                        yield Label("關鍵字")
                        with Container(classes="path-row"):
                            yield ShortcutInput(
                                placeholder="例如：Order_123 或 SQLException",
                                id="keyword",
                            )
                            yield Button("清除", id="clear_keyword")
                    yield Static("", classes="field-hint")

                    yield Button("展開進階設定", id="toggle_advanced_settings", classes="advanced-toggle")
                    yield Static("", classes="field-hint")
                    with Container(id="advanced-settings", classes="advanced-settings"):
                        with Container(id="advanced-settings-title-row"):
                            yield Static("進階設定", classes="section-title section-title--sub")

                        with Container(classes="field"):
                            yield Label("輸出檔名")
                            with Container(classes="field-body"):
                                yield ShortcutInput(
                                    placeholder="例如：analysis_123000",
                                    value=self._auto_output_name,
                                    id="output_name",
                                )
                                yield Static("副檔名由輸出格式自動決定", classes="field-hint")

                        with Container(classes="field"):
                            yield Label("Logback XML")
                            with Container(classes="path-row compact-buttons"):
                                yield ShortcutInput(
                                    placeholder="例如：./logback-spring.xml",
                                    id="logback_xml_path",
                                )
                                yield Button("瀏覽", id="browse_logback_xml")
                        yield Static("", classes="field-hint", id="logback-xml-status")

                        with Container(classes="field"):
                            yield Label("Log 格式")
                            yield Select(
                                [
                                    ("預設 Logback", "default"),
                                    ("進階 Pattern", "custom"),
                                ],
                                value="default",
                                id="pattern_mode",
                            )
                        yield Static("", classes="field-hint")

                        with Container(classes="field", id="pattern-field"):
                            yield Label("Pattern")
                            with Container(classes="field-body"):
                                yield ShortcutInput(
                                    placeholder=DEFAULT_LOGBACK_PATTERN,
                                    id="log_pattern",
                                )
                                yield Static("進階模式才會套用；僅支援常見 Logback token", classes="field-hint")

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

                        with Container(classes="field-row"):
                            with Container(classes="field field--inline field--ignore"):
                                yield Label("忽略大小寫")
                                yield Checkbox(value=True, id="ignore_case")

                            with Container(classes="field field--inline field--format"):
                                yield Label("輸出格式")
                                yield Select(
                                    [
                                        ("CSV (Excel)", "csv"),
                                        ("JSON", "json"),
                                        ("Markdown", "md"),
                                    ],
                                    value="csv",
                                    id="format",
                                )
                        yield Static("", classes="path-status", id="path-status")

                        with Container(classes="field-row"):
                            with Container(classes="field field--inline field--sort"):
                                yield Label("排序方式")
                                yield Select(
                                    [
                                        ("時間排序", "time"),
                                        ("Level 分組", "level"),
                                    ],
                                    value="time",
                                    id="sort_by",
                                )

                            with Container(classes="field field--inline field--display-mode"):
                                yield Label("顯示模式")
                                yield Select(
                                    [
                                        ("預設摘要", "full"),
                                        ("濃縮摘要", "summary"),
                                    ],
                                    value="summary",
                                    id="display_mode",
                                )
                        yield Static("", classes="field-hint")

                    with Container(id="actions"):
                        yield Button("開始分析", variant="primary", id="run")
                        yield Button("清除結果", id="clear")

                    yield Static(
                        "快捷鍵：Enter 開始分析，c 清除結果，q 離開",
                        classes="helper",
                    )

                with Container(id="output-panel"):
                    yield Static("執行結果", classes="section-title")
                    yield RichLog(
                        id="result-box",
                        markup=True,
                        highlight=False,
                        auto_scroll=False,
                        wrap=True,
                        min_width=0,
                    )

        yield Footer()

    def on_mount(self) -> None:
        path_input = self.query_one("#path", Input)
        output_path_input = self.query_one("#output_path", Input)
        self.query_one("#result-box", RichLog).can_focus = False
        self._restore_recent_form_state()
        self._sync_pattern_field_visibility()
        self.call_after_refresh(self.set_focus, None)
        self._set_advanced_settings_visible(False)
        self._sync_layout(self.size.width, self.size.height)
        self.update_path_preview(path_input.value)
        self.update_path_preview(output_path_input.value, "#output-path-status", require_writable=True)

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
        actions = self.query_one("#actions", Container)
        content = self.query_one("#content", ScrollableContainer)
        hero_title_row = self.query_one("#hero-title-row", Container)

        if compact:
            content.remove_class("wide")
            content.add_class("compact")
            form_panel.remove_class("wide")
            form_panel.add_class("compact")
            actions.remove_class("wide")
            actions.add_class("compact")
            hero_title_row.remove_class("wide")
            hero_title_row.add_class("compact")
        else:
            content.remove_class("compact")
            content.add_class("wide")
            form_panel.remove_class("compact")
            form_panel.add_class("wide")
            actions.remove_class("compact")
            actions.add_class("wide")
            hero_title_row.remove_class("compact")
            hero_title_row.add_class("wide")

    def _is_compact(self, width: int, height: int) -> bool:
        # 當寬度小於 90，或者高度小於 18 時，才使用單欄（compact）垂直版面
        # 否則使用雙欄（左右分欄）版面
        return width < 90 or height < 18

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "path":
            self.update_path_preview(event.value)
        elif event.input.id == "output_path":
            self.update_path_preview(event.value, "#output-path-status", require_writable=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {
            "path",
            "output_path",
            "output_name",
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
        if event.button.id == "run":
            await self.action_run_analysis()
        elif event.button.id == "clear":
            self.action_clear_result()
        elif event.button.id == "toggle_advanced_settings":
            self.action_toggle_advanced_settings()
        elif event.button.id == "browse_path":
            self.action_browse_path()
        elif event.button.id == "browse_output_path":
            self.action_browse_output_path()
        elif event.button.id == "clear_keyword":
            self.action_clear_keyword()
        elif event.button.id == "browse_logback_xml":
            self.action_browse_logback_xml()

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

    def _apply_selected_directory(self, target_id: str, selected_path: Optional[str]) -> None:
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

    def _apply_selected_logback_xml(self, selected_path: Optional[str]) -> None:
        if not selected_path:
            return

        xml_input = self.query_one("#logback_xml_path", Input)
        xml_input.value = selected_path
        xml_input.focus()

    def _apply_and_load_logback_xml(self, selected_path: Optional[str]) -> None:
        self._apply_selected_logback_xml(selected_path)
        if selected_path:
            self.action_load_logback_xml()

    async def action_run_analysis(self) -> None:
        run_button = self.query_one("#run", Button)
        clear_button = self.query_one("#clear", Button)
        result_box = self.query_one("#result-box", RichLog)

        # 記錄當下聚焦的元件，並暫時清除焦點，避免焦點自動轉移至「清除結果」按鈕造成反白
        original_focused = self.focused
        self.set_focus(None)

        # 停用按鈕以防二次點擊
        run_button.disabled = True
        clear_button.disabled = True
        self._set_result(result_box, build_loading_view())

        try:
            self._autofill_logback_settings(self.query_one("#path", Input).value.strip() or ".")
            (
                path,
                output_path,
                output_name,
                start_date_text,
                start_time_text,
                end_date_text,
                end_time_text,
                keyword,
                pattern_mode,
                log_pattern,
                display_mode,
                sort_by,
                ignore_case,
                fmt,
            ) = self._collect_form_values()
            start_dt, end_dt = parse_datetime_range_inputs(
                start_date_text,
                start_time_text,
                end_date_text,
                end_time_text,
            )
            self._save_recent_form_state(
                {
                    "path": path,
                    "output_path": output_path,
                    "keyword": keyword,
                    "ignore_case": ignore_case,
                    "display_mode": display_mode,
                    "sort_by": sort_by,
                    "format": fmt,
                    "start_date": start_date_text,
                    "start_time": start_time_text,
                    "end_date": end_date_text,
                    "end_time": end_time_text,
                }
            )
            normalized_output = build_output_path(output_path, output_name, fmt)
            selected_pattern = log_pattern if pattern_mode == "custom" else None
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                partial(
                    run_analysis,
                    path,
                    normalized_output,
                    start_dt,
                    end_dt,
                    keyword or None,
                    ignore_case,
                    sort_by,
                    fmt,
                    selected_pattern,
                    None,
                    False,
                ),
            )
            self._last_result = result
            self._set_result(
                result_box,
                build_dashboard_view(result, self._should_compact_dashboard(self.size.width, self.size.height)),
            )
            self._refresh_output_name_default(output_name)
        except PermissionError as exc:
            self._last_result = None
            self._set_result(result_box, build_error_view("權限不足", str(exc)))
        except FileNotFoundError as exc:
            self._last_result = None
            self._set_result(result_box, build_error_view("找不到資料夾", str(exc)))
        except ValueError as exc:
            self._last_result = None
            title = "無可分析資料" if str(exc).startswith("找不到符合條件的 log") else "輸入錯誤"
            self._set_result(result_box, build_error_view(title, str(exc)))
        except Exception as exc:
            self._last_result = None
            self._set_result(result_box, build_error_view("執行失敗", str(exc)))
        finally:
            run_button.disabled = False
            clear_button.disabled = False
            # 恢復原本的焦點元件
            if original_focused and not original_focused.disabled:
                self.set_focus(original_focused)

    def action_clear_result(self) -> None:
        result_box = self.query_one("#result-box", RichLog)
        self._last_result = None
        self._set_result(result_box, build_idle_view())

    def action_clear_keyword(self) -> None:
        keyword_input = self.query_one("#keyword", Input)
        keyword_input.value = ""
        keyword_input.focus()

    def action_toggle_advanced_settings(self) -> None:
        self._set_advanced_settings_visible(not self._advanced_settings_visible)

    def _set_advanced_settings_visible(self, visible: bool) -> None:
        self._advanced_settings_visible = visible
        advanced_settings = self.query_one("#advanced-settings", Container)
        advanced_settings.display = visible

        toggle_button = self.query_one("#toggle_advanced_settings", Button)
        toggle_button.label = "收合進階設定" if visible else "展開進階設定"

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
        normalized_dir = os.path.abspath(os.path.expanduser(log_dir.strip() or "."))
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

    def _collect_form_values(self) -> Tuple[str, str, str, str, str, str, str, str, str, str, str, str, bool, str]:
        path = self.query_one("#path", Input).value.strip() or "."
        output_path = self.query_one("#output_path", Input).value.strip() or "."
        output_name = self.query_one("#output_name", Input).value.strip()
        start_date = self.query_one("#start_date", Input).value.strip()
        start_time = self.query_one("#start_time", Input).value.strip()
        end_date = self.query_one("#end_date", Input).value.strip()
        end_time = self.query_one("#end_time", Input).value.strip()
        keyword = self.query_one("#keyword", Input).value.strip()
        pattern_mode = self.query_one("#pattern_mode", Select).value or "default"
        log_pattern = self.query_one("#log_pattern", Input).value.strip()
        display_mode = self.query_one("#display_mode", Select).value or "summary"
        sort_by = self.query_one("#sort_by", Select).value or "time"
        ignore_case = self.query_one("#ignore_case", Checkbox).value
        fmt = self.query_one("#format", Select).value or "csv"
        return (
            path,
            output_path,
            output_name,
            start_date,
            start_time,
            end_date,
            end_time,
            keyword,
            str(pattern_mode),
            log_pattern,
            str(display_mode),
            str(sort_by),
            ignore_case,
            str(fmt),
        )

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

    def _save_recent_form_state(self, state: dict[str, object]) -> None:
        save_recent_tui_state(state)


if __name__ == "__main__":
    app = LogAnalyzerApp()
    app.run()
