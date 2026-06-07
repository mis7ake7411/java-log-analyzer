from __future__ import annotations

import asyncio
import os
from importlib.metadata import PackageNotFoundError, version as package_version
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, ScrollableContainer
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DirectoryTree, Footer, Header, Input, Label, RichLog, Select, Static

from .exporter import export_results
from .parser import parse_logs


@dataclass
class AnalysisResult:
    input_path: str
    output_path: str
    format_name: str
    keyword: str
    ignore_case: bool
    total_logs: int
    matched_groups: int
    matched_occurrences: int
    level_summary: List[Tuple[str, int]]


def inspect_directory_path(path: str, require_writable: bool = False) -> tuple[str, str, bool, str]:
    """檢查資料夾路徑是否存在、可讀，必要時也要可寫。"""
    cleaned = path.strip()
    if not cleaned:
        return "yellow", "請輸入資料夾路徑。", False, ""

    abspath = os.path.abspath(os.path.expanduser(cleaned))
    if not os.path.exists(abspath):
        return "red", f"路徑不存在：{abspath}", False, abspath
    if not os.path.isdir(abspath):
        return "red", f"不是資料夾：{abspath}", False, abspath
    required_mode = os.R_OK | os.X_OK
    if require_writable:
        required_mode |= os.W_OK
    if not os.access(abspath, required_mode):
        if require_writable:
            return "red", f"權限不足，無法寫入：{abspath}", False, abspath
        return "red", f"權限不足，無法讀取：{abspath}", False, abspath
    label = "目標資料夾" if not require_writable else "輸出資料夾"
    return "green", f"{label}：{abspath}", True, abspath


def ensure_readable_directory(path: str) -> str:
    """確認分析來源資料夾可讀，並回傳正規化後路徑。"""
    color, message, is_valid, abspath = inspect_directory_path(path)
    if is_valid:
        return abspath
    if color == "red" and "權限不足" in message:
        raise PermissionError(message)
    raise FileNotFoundError(message)


def ensure_writable_directory(path: str) -> str:
    """確認輸出資料夾可寫，並回傳正規化後路徑。"""
    color, message, is_valid, abspath = inspect_directory_path(path, require_writable=True)
    if is_valid:
        return abspath
    if color == "red" and "權限不足" in message:
        raise PermissionError(message)
    raise FileNotFoundError(message)


def get_system_root_path() -> Path:
    """回傳目前作業系統的根目錄。"""
    return Path(Path.cwd().anchor or os.sep)


def get_default_start_date_text() -> str:
    """回傳起始日期預設值。"""
    return date.today().isoformat()


def get_default_start_time_text() -> str:
    """回傳起始時間預設值。"""
    return "00:00"


def get_default_end_date_text() -> str:
    """回傳結束日期預設值。"""
    return ""


def get_default_end_time_text() -> str:
    """回傳結束時間預設值。"""
    return ""


def parse_datetime_range_inputs(
    start_date_text: str,
    start_time_text: str,
    end_date_text: str,
    end_time_text: str,
) -> tuple[datetime, Optional[datetime]]:
    """將分離的日期與時間欄位組合成起訖時間。"""
    start_date_clean = start_date_text.strip() or get_default_start_date_text()
    start_time_clean = start_time_text.strip() or get_default_start_time_text()
    start_dt = datetime.strptime(f"{start_date_clean} {start_time_clean}", "%Y-%m-%d %H:%M")

    end_date_clean = end_date_text.strip()
    end_time_clean = end_time_text.strip()
    if not end_date_clean and not end_time_clean:
        return start_dt, None
    if not end_date_clean or not end_time_clean:
        raise ValueError("請同時輸入結束日期與時間，或兩者都留白。")

    end_dt = datetime.strptime(f"{end_date_clean} {end_time_clean}", "%Y-%m-%d %H:%M")
    if start_dt > end_dt:
        raise ValueError("開始時間不能晚於結束時間。")
    return start_dt, end_dt


def get_package_version() -> str:
    """讀取目前安裝的套件版本，找不到時回退到未知。"""
    try:
        return package_version("java-log-analyzer")
    except PackageNotFoundError:
        return "unknown"


class FolderOnlyDirectoryTree(DirectoryTree):
    """只保留資料夾節點，避免選到檔案。"""
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir()]


class DirectoryPickerScreen(ModalScreen[Optional[str]]):
    """資料夾選擇彈窗，支援樹狀瀏覽與手動輸入。"""
    def __init__(
        self,
        initial_path: str,
        title: str,
        hint: str,
        confirm_label: str,
        require_writable: bool = False,
    ) -> None:
        self.initial_path = initial_path
        self.title_text = title
        self.hint_text = hint
        self.confirm_label = confirm_label
        self.require_writable = require_writable
        self._selected_path: Optional[str] = None
        super().__init__()

    def compose(self) -> ComposeResult:
        root_path, status_message = self._pick_tree_root()
        with Container(id="directory-picker"):
            with Container(id="picker-card"):
                # 標題、提示與樹狀瀏覽放在同一個彈窗中，方便快速切換。
                yield Static(self.title_text, id="picker-title")
                yield Static(self.hint_text, id="picker-hint")
                yield FolderOnlyDirectoryTree(root_path, id="picker-tree")
                with Container(id="picker-manual"):
                    yield Label("手動輸入")
                    yield Input(value=self.initial_path or str(root_path), id="picker-path")
                yield Static(status_message, id="picker-status")
                with Container(id="picker-actions"):
                    yield Button(self.confirm_label, variant="primary", id="picker-confirm")
                    yield Button("取消", id="picker-cancel")

    def on_mount(self) -> None:
        self.query_one("#picker-path", Input).focus()

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        selected_path = str(event.path)
        self._selected_path = selected_path
        picker_path = self.query_one("#picker-path", Input)
        picker_path.value = selected_path
        color, message, _, _ = inspect_directory_path(selected_path, require_writable=self.require_writable)
        self.query_one("#picker-status", Static).update(self._status_text(message, color))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-cancel":
            self.dismiss(None)
            return
        if event.button.id == "picker-confirm":
            self._confirm_selection()

    def _confirm_selection(self) -> None:
        picker_path = self.query_one("#picker-path", Input)
        selected_path = picker_path.value.strip() or self._selected_path or ""
        # 確認前再檢查一次權限，避免樹狀瀏覽與實際可用狀態不同步。
        color, message, is_valid, _ = inspect_directory_path(selected_path, require_writable=self.require_writable)
        if not is_valid:
            self.query_one("#picker-status", Static).update(self._status_text(message, color))
            return
        self.dismiss(os.path.abspath(selected_path))

    def _pick_tree_root(self) -> tuple[Path, str]:
        # 瀏覽樹預設從系統根目錄開始，避免一開啟就被目前專案路徑綁住。
        root_path = get_system_root_path()
        return root_path, ""

    def _status_text(self, message: str, color: str) -> Text:
        text = Text()
        text.append(message, style=color)
        return text


class LogAnalyzerApp(App):
    TITLE = "Java Log Analyzer"
    CSS_PATH = [
        "./tcss/tui.base.tcss",
        "./tcss/tui.layout.tcss",
        "./tcss/tui.dialog.tcss",
        "./tcss/tui.responsive.tcss",
    ]
    _last_result: Optional[AnalysisResult] = None
    BINDINGS = [
        ("q", "quit", "離開"),
        ("enter", "run_analysis", "開始分析"),
        ("c", "clear_result", "清除結果"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="shell"):
            with Container(id="hero"):
                with Container(id="hero-title-row"):
                    yield Static("Java Log Analyzer", id="hero-title")
                    yield Static(f"v{get_package_version()}", id="hero-version")
                    yield Static("·", id="hero-separator")
                    yield Static("分析、篩選並匯出 Java Logback 記錄", id="hero-subtitle")

            with ScrollableContainer(id="content"):
                with ScrollableContainer(id="form-panel"):
                    yield Static("輸入設定", classes="section-title")

                    with Container(classes="field"):
                        yield Label("Log 目錄")
                        with Container(classes="path-row"):
                            yield Input(placeholder="例如：./logs", value=".", id="path")
                            yield Button("瀏覽", id="browse_path")
                    yield Static("", classes="path-status", id="path-status")

                    with Container(classes="field"):
                        yield Label("目標資料夾")
                        with Container(classes="path-row"):
                            yield Input(placeholder="例如：./exports", value=".", id="output_path")
                            yield Button("瀏覽", id="browse_output_path")
                    yield Static("", classes="path-status", id="output-path-status")

                    with Container(classes="field"):
                        yield Label("輸出檔名")
                        with Container(classes="field-body"):
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            yield Input(
                                placeholder="例如：analysis_123000",
                                value=f"analysis_{timestamp}",
                                id="output_name",
                            )
                            yield Static("副檔名由輸出格式自動決定。", classes="field-hint")

                    with Container(classes="field"):
                        yield Label("關鍵字")
                        with Container(classes="path-row"):
                            yield Input(
                                placeholder="例如：Order_123 或 SQLException",
                                id="keyword",
                            )
                            yield Button("清除", id="clear_keyword")
                    yield Static("", classes="field-hint")

                    with Container(classes="time-section"):
                        yield Label("時間區間")
                        with Container(classes="time-stack"):
                            with Container(id="start-time-row", classes="time-row"):
                                with Container(id="start-date-group", classes="time-group"):
                                    yield Label("開始日期")
                                    yield Input(
                                        value=get_default_start_date_text(),
                                        placeholder="YYYY-MM-DD",
                                        id="start_date",
                                    )
                                with Container(id="start-time-group", classes="time-group"):
                                    yield Label("開始時間")
                                    yield Input(
                                        value=get_default_start_time_text(),
                                        placeholder="HH:MM",
                                        id="start_time",
                                    )
                            with Container(id="end-time-row", classes="time-row"):
                                with Container(id="end-date-group", classes="time-group"):
                                    yield Label("結束日期")
                                    yield Input(
                                        value=get_default_end_date_text(),
                                        placeholder="YYYY-MM-DD",
                                        id="end_date",
                                    )
                                with Container(id="end-time-group", classes="time-group"):
                                    yield Label("結束時間")
                                    yield Input(
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
                    yield Static("", classes="field-hint")

                    with Container(id="actions"):
                        yield Button("開始分析", variant="primary", id="run")
                        yield Button("清除結果", id="clear")

                    yield Static(
                        "快捷鍵：Enter 開始分析，c 清除結果，q 離開。",
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
        path_input.focus()
        self._sync_layout(self.size.width, self.size.height)
        self.update_path_preview(path_input.value)
        self.update_path_preview(output_path_input.value, "#output-path-status", require_writable=True)

    def on_resize(self, event: Resize) -> None:
        self._sync_layout(event.size.width, event.size.height)

        last_result = getattr(self, "_last_result", None)
        if last_result is not None:
            result_box = self.query_one("#result-box", RichLog)
            self._set_result(result_box, self._build_dashboard(last_result))
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
        # 當寬度小於 90，或者高度小於 18 時，才使用單欄（compact）垂直版面。
        # 否則使用雙欄（左右分欄）版面。
        return width < 90 or height < 18

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "path":
            self.update_path_preview(event.value)
        elif event.input.id == "output_path":
            self.update_path_preview(event.value, "#output-path-status", require_writable=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"path", "output_path", "output_name", "keyword", "start_date", "start_time", "end_date", "end_time"}:
            await self.action_run_analysis()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            await self.action_run_analysis()
        elif event.button.id == "clear":
            self.action_clear_result()
        elif event.button.id == "browse_path":
            self.action_browse_path()
        elif event.button.id == "browse_output_path":
            self.action_browse_output_path()
        elif event.button.id == "clear_keyword":
            self.action_clear_keyword()

    def update_path_preview(self, path: str, status_id: str = "#path-status", require_writable: bool = False) -> None:
        preview = self.query_one(status_id, Static)
        # 狀態列即時反映路徑可用性，避免要按執行才發現權限或拼字錯誤。
        color, message, _, _ = inspect_directory_path(path, require_writable=require_writable)
        preview.update(self._compact_path_status(message, color, require_writable=require_writable))

    def action_browse_path(self) -> None:
        self._open_directory_picker(
            target_id="path",
            title="選擇 Log 目錄",
            hint="可用樹狀瀏覽，或直接手動輸入路徑。",
            confirm_label="使用此路徑",
            require_writable=False,
        )

    def action_browse_output_path(self) -> None:
        self._open_directory_picker(
            target_id="output_path",
            title="選擇目標資料夾",
            hint="可用樹狀瀏覽，或直接手動輸入路徑。",
            confirm_label="使用此資料夾",
            require_writable=True,
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
        # 瀏覽成功後直接回填到對應欄位，手動輸入仍保留為 fallback。
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
        self._set_result(result_box, self._loading_view())

        try:
            (
                path,
                output_path,
                output_name,
                start_date_text,
                start_time_text,
                end_date_text,
                end_time_text,
                keyword,
                ignore_case,
                fmt,
            ) = self._collect_form_values()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                self._execute_analysis,
                path,
                output_path,
                output_name,
                start_date_text,
                start_time_text,
                end_date_text,
                end_time_text,
                keyword,
                ignore_case,
                fmt,
            )
            self._last_result = result
            self._set_result(result_box, self._build_dashboard(result))
        except PermissionError as exc:
            self._last_result = None
            self._set_result(result_box, self._error_view("權限不足", str(exc)))
        except FileNotFoundError as exc:
            self._last_result = None
            self._set_result(result_box, self._error_view("找不到資料夾", str(exc)))
        except ValueError as exc:
            self._last_result = None
            title = "無可分析資料" if str(exc).startswith("找不到符合條件的 log") else "輸入錯誤"
            self._set_result(result_box, self._error_view(title, str(exc)))
        except Exception as exc:
            self._last_result = None
            self._set_result(result_box, self._error_view("執行失敗", str(exc)))
        finally:
            run_button.disabled = False
            clear_button.disabled = False
            # 恢復原本的焦點元件
            if original_focused and not original_focused.disabled:
                self.set_focus(original_focused)

    def action_clear_result(self) -> None:
        result_box = self.query_one("#result-box", RichLog)
        self._last_result = None
        self._set_result(result_box, self._build_idle_view())

    def action_clear_keyword(self) -> None:
        keyword_input = self.query_one("#keyword", Input)
        keyword_input.value = ""
        keyword_input.focus()

    def _collect_form_values(self) -> Tuple[str, str, str, str, str, str, str, str, bool, str]:
        path = self.query_one("#path", Input).value.strip() or "."
        output_path = self.query_one("#output_path", Input).value.strip() or "."
        output_name = self.query_one("#output_name", Input).value.strip()
        start_date = self.query_one("#start_date", Input).value.strip()
        start_time = self.query_one("#start_time", Input).value.strip()
        end_date = self.query_one("#end_date", Input).value.strip()
        end_time = self.query_one("#end_time", Input).value.strip()
        keyword = self.query_one("#keyword", Input).value.strip()
        ignore_case = self.query_one("#ignore_case", Checkbox).value
        fmt = self.query_one("#format", Select).value or "csv"
        return path, output_path, output_name, start_date, start_time, end_date, end_time, keyword, ignore_case, fmt

    def _execute_analysis(
        self,
        path: str,
        output_path: str,
        output_name: str,
        start_date_text: str,
        start_time_text: str,
        end_date_text: str,
        end_time_text: str,
        keyword: str,
        ignore_case: bool,
        fmt: str,
    ) -> AnalysisResult:
        normalized_path = ensure_readable_directory(path)
        normalized_output = self._normalize_output_path(output_path, output_name, fmt)
        start_dt, end_dt = parse_datetime_range_inputs(
            start_date_text,
            start_time_text,
            end_date_text,
            end_time_text,
        )
        counts, matched_logs = parse_logs(
            normalized_path,
            start_dt,
            end_dt,
            keyword or None,
            ignore_case=ignore_case,
        )

        if not counts and not matched_logs:
            raise ValueError("找不到符合條件的 log。請確認目錄、關鍵字或資料內容。")

        export_results(counts, matched_logs, normalized_output, fmt)

        total_logs = sum(counts.values())
        matched_groups = len(matched_logs)
        matched_occurrences = sum(entry.get("count", 1) for entry in matched_logs)
        level_summary = [(level, count) for level, count in sorted(counts.items()) if count > 0]

        return AnalysisResult(
            input_path=os.path.abspath(normalized_path),
            output_path=os.path.abspath(normalized_output),
            format_name=fmt,
            keyword=keyword or "未設定",
            ignore_case=ignore_case,
            total_logs=total_logs,
            matched_groups=matched_groups,
            matched_occurrences=matched_occurrences,
            level_summary=level_summary,
        )

    def _normalize_output_path(self, output_path: str, output_name: str, fmt: str) -> str:
        normalized_dir = ensure_writable_directory(output_path)
        cleaned = output_name.strip()
        if not cleaned:
            cleaned = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 使用者只輸入檔名，副檔名由輸出格式統一決定。
        cleaned = os.path.basename(cleaned)
        base_name, _ = os.path.splitext(cleaned)
        if not base_name:
            base_name = cleaned
        return os.path.join(normalized_dir, f"{base_name}.{fmt}")

    def _build_idle_view(self) -> Panel:
        body = Text()
        body.append("尚未產生分析結果\n", style="bold cyan")
        body.append("\n")
        body.append("請先在上方填入條件，然後按「開始分析」。\n", style="white")
        body.append("\n")
        body.append("Enter 開始分析，c 清除結果，q 離開。", style="dim")
        return Panel(body, title="結果區", border_style="cyan", padding=(1, 2))

    def _loading_view(self) -> Panel:
        body = Text("分析中，請稍候...", style="bold cyan")
        return Panel(body, title="執行中", border_style="cyan", padding=(1, 2))

    def _status_text(self, message: str, color: str) -> Text:
        text = Text()
        text.append(message, style=color)
        return text

    def _compact_path_status(self, message: str, color: str, require_writable: bool) -> Text:
        if color == "green":
            label = "可寫" if require_writable else "可讀"
            return self._status_text(f"狀態：{label}", color)
        return self._status_text(message, color)

    def _set_result(self, result_box: RichLog, renderable: object) -> None:
        result_box.clear()
        result_box.write(renderable)
        result_box.scroll_home()

    def _error_view(self, title: str, message: str) -> Panel:
        body = Text()
        body.append(f"{title}\n", style="bold red")
        body.append(message, style="white")
        return Panel(body, title="執行失敗", border_style="red", padding=(1, 2))

    def _build_dashboard(self, result: AnalysisResult) -> Group:
        compact = self._is_compact(self.size.width, self.size.height)
        overview = Panel(
            Group(
                Text("分析完成", style="bold green"),
                Text("以下以單欄卡片方式呈現本次分析結果。", style="dim"),
            ),
            border_style="green",
            padding=(1, 2),
            title="執行狀態",
        )

        metric_cards = [
            self._metric_card("總 Log", str(result.total_logs), "cyan", "整體筆數"),
            self._metric_card("群組", str(result.matched_groups), "green", "符合條件的事件群組"),
            self._metric_card("命中", str(result.matched_occurrences), "yellow", "條件觸發總次數"),
            self._metric_card("格式", result.format_name.upper(), "magenta", "輸出檔案格式"),
        ]

        metadata = Table.grid(padding=(0, 1))
        metadata.add_column(style="bold cyan", width=12)
        metadata.add_column(ratio=1)
        metadata.add_row("輸入目錄", result.input_path)
        metadata.add_row("輸出檔案", result.output_path)
        metadata.add_row("關鍵字", result.keyword)
        metadata.add_row("忽略大小寫", "是" if result.ignore_case else "否")

        level_table = Table.grid(expand=True)
        level_table.add_column(style="bold", width=12)
        level_table.add_column(justify="right", width=8)
        level_table.add_column(ratio=1)
        if result.level_summary:
            max_count = max(count for _, count in result.level_summary)
            for level, count in result.level_summary:
                level_table.add_row(level, str(count), self._progress_bar(count, max_count))
        else:
            level_table.add_row("N/A", "-", "沒有可顯示的統計")

        if compact:
            return Group(
                overview,
                *metric_cards,
                Panel(metadata, title="分析資訊", border_style="blue", padding=(1, 2)),
                Panel(level_table, title="Level 分布", border_style="green", padding=(1, 2)),
            )

        return Group(
            overview,
            Columns(metric_cards, equal=True, expand=True),
            Columns(
                [
                    Panel(metadata, title="分析資訊", border_style="blue", padding=(1, 2)),
                    Panel(level_table, title="Level 分布", border_style="green", padding=(1, 2)),
                ],
                equal=True,
                expand=True,
            ),
        )

    def _metric_card(self, title: str, value: str, accent: str, caption: str) -> Panel:
        body = Group(
            Text(value, style=f"bold {accent}"),
            Text(caption, style="dim"),
        )
        return Panel(body, title=title, border_style=accent, padding=(1, 2))

    def _progress_bar(self, value: int, maximum: int, width: int = 18) -> str:
        if maximum <= 0:
            return "░" * width
        filled = max(1, round((value / maximum) * width))
        filled = min(filled, width)
        return "█" * filled + "░" * (width - filled)


if __name__ == "__main__":
    app = LogAnalyzerApp()
    app.run()
