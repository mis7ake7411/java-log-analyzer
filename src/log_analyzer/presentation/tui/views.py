from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ...application.analysis_service import AnalysisResult
from ...application.dashboard_summary import DashboardAnalysis, aggregate_dashboard_summary
from ...domain.log_types import ScanDiagnostics
from ..encoding_warnings import format_encoding_warning
from ..error_messages import get_error_hint


def analyze_dashboard(result: AnalysisResult) -> DashboardAnalysis:
    """使用已保留的摘要，或由舊版呼叫者提供的明細計算摘要。"""
    dashboard_summary = getattr(result, "dashboard_summary", None)
    if dashboard_summary is not None:
        return dashboard_summary
    dashboard_summary, _ = aggregate_dashboard_summary(result.matched_logs or ())
    return dashboard_summary


def build_idle_view() -> Panel:
    return _build_status_panel(
        "結果區",
        "cyan",
        (
            Text("尚未產生分析結果", style="bold cyan"),
            Text("請先在上方填入條件，然後按「開始分析」", style="white"),
            Text("Enter 開始分析，c 清除結果，q 離開", style="dim"),
        ),
    )


def build_loading_view() -> Panel:
    return _build_status_panel(
        "執行中",
        "cyan",
        (
            Text("分析中，請稍候...", style="bold cyan"),
            Text("正在掃描 Log 目錄並套用條件", style="white"),
            Text("大型資料夾可能需要較久時間", style="dim"),
        ),
    )


def build_error_view(title: str, message: str) -> Panel:
    return _build_status_panel(
        "執行失敗",
        "red",
        (
            Text(title, style="bold red"),
            Text(message, style="white"),
            Text(get_error_hint(title, message), style="dim"),
        ),
    )


def build_zero_result_view(diagnostics: ScanDiagnostics) -> Panel:
    if diagnostics.files_scanned == 0:
        reason = "找不到支援的 Log 檔案，請確認輸入目錄。"
    elif diagnostics.parsed_entries == 0:
        reason = "找到支援的 Log 檔案，但沒有內容符合目前的 Logback Pattern。"
    elif diagnostics.after_time == 0:
        reason = "沒有事件通過時間解析或時間範圍，請確認日誌時間與條件。"
    elif diagnostics.after_level == 0:
        reason = "Level 篩選排除了所有時間範圍內的事件。"
    elif diagnostics.after_keyword == 0:
        reason = "關鍵字篩選排除了所有 Level 事件。"
    else:
        reason = "目前沒有可顯示的分析結果。"

    panel = _build_status_panel(
        "沒有符合資料",
        "yellow",
        (
            Text("沒有符合資料", style="bold yellow"),
            Text(reason, style="white"),
            Text(f"掃描 Log 檔案：{diagnostics.files_scanned}", style="cyan"),
            Text(f"符合 Pattern 的事件：{diagnostics.parsed_entries}", style="cyan"),
            Text(f"時間範圍後：{diagnostics.after_time}", style="cyan"),
            Text(f"Level 後：{diagnostics.after_level}", style="cyan"),
            Text(f"關鍵字後：{diagnostics.after_keyword}", style="cyan"),
        ),
    )
    encoding_warning = format_encoding_warning(diagnostics)
    if encoding_warning:
        panel.renderable = Group(panel.renderable, Text(encoding_warning, style="yellow"))
    return panel


def format_path_status(message: str, color: str, require_writable: bool) -> Text:
    if color == "green":
        label = "可寫" if require_writable else "可讀"
        return _status_text(f"狀態：{label}", color)
    return _status_text(message, color)


def build_dashboard_view(result: AnalysisResult, compact: bool) -> Group:
    analysis = analyze_dashboard(result)
    has_export = bool(result.exported_files)
    export_state = (
        f"已匯出 {result.format_name.upper()}"
        if has_export
        else "未匯出"
    )
    status_line = Text("分析完成", style="bold green")
    status_line.append(" · ")
    status_line.append(export_state, style="magenta" if has_export else "dim")
    overview = Panel(
        status_line,
        border_style="green",
        padding=(0, 1),
        title="執行狀態",
        expand=True,
    )
    exception_panel = _build_exception_summary_panel(analysis.exception_summary)
    hotspot_panel = _build_time_hotspot_panel(analysis.time_hotspots)
    distribution_panel = _build_logger_thread_distribution_panel(
        analysis.logger_summary,
        analysis.thread_summary,
    )

    metric_cards = [
        _metric_card("總 Log", str(result.total_logs), "cyan", "整體筆數"),
        _metric_card("群組", str(result.matched_groups), "green", "符合條件的事件群組"),
        _metric_card("命中", str(result.matched_occurrences), "yellow", "條件觸發總次數"),
    ]

    metadata = Table.grid(padding=(0, 1), expand=True)
    metadata.add_column(style="bold cyan", width=12)
    metadata.add_column(ratio=1, no_wrap=False, overflow="fold")
    encoding_warning = format_encoding_warning(getattr(result, "diagnostics", None))
    if encoding_warning:
        metadata.add_row("讀取提醒", Text(encoding_warning, style="yellow"))
    analysis_request = getattr(result, "analysis_request", None)
    selected_path = getattr(analysis_request, "path", None)
    metadata.add_row("Log 來源", Text(selected_path or result.input_path))
    metadata.add_row(
        "輸出檔案",
        Text("\n".join(result.exported_files) if has_export else "尚未匯出"),
    )
    if len(result.exported_files) > 1:
        metadata.add_row("分割檔案", f"{len(result.exported_files)} 個")
    metadata.add_row("關鍵字", Text(result.keyword))
    levels = getattr(result, "selected_levels", ())
    selected_levels = ", ".join(levels) if levels else "全部"
    metadata.add_row("Log 層級", Text(selected_levels))
    metadata.add_row("排序方式", "Level 分組" if result.sort_by == "level" else "時間排序")
    metadata.add_row("忽略大小寫", "是" if result.ignore_case else "否")

    level_table = Table.grid(expand=True, padding=(0, 1))
    level_table.add_column(style="bold", width=7, no_wrap=True)
    level_table.add_column(justify="right", width=7, no_wrap=True)
    level_table.add_column(justify="right", width=8, no_wrap=True)
    level_table.add_column(ratio=1, min_width=1)
    level_table.add_row(
        Text("Level", style="dim"),
        Text("筆數", style="dim"),
        Text("占比", style="dim"),
        Text("相對最高", style="dim"),
    )
    if result.level_summary:
        max_count = max(count for _, count in result.level_summary)
        total_count = sum(count for _, count in result.level_summary)
        for level, count in result.level_summary:
            percentage = (count / total_count * 100) if total_count else 0
            level_table.add_row(
                Text(str(level)),
                str(count),
                f"{percentage:.1f}%",
                _ProgressBar(count, max_count),
            )
    else:
        level_table.add_row("N/A", "-", "", "沒有可顯示的統計")

    level_content = Group(
        level_table,
        Text(
            "占比以本次結果的總筆數計算；長條以最高筆數為滿格。",
            style="dim",
        ),
    )

    summary_panel_items = [
        Panel(
            metadata,
            title="分析資訊",
            border_style="blue",
            padding=(0, 1),
            expand=True,
        ),
        Panel(
            level_content,
            title="Level 分布",
            border_style="green",
            padding=(0, 1),
            expand=True,
        ),
    ]
    summary_panels = Group(*summary_panel_items, fit=False)

    return Group(
        overview,
        _ResponsivePanelRow(metric_cards, minimum_panel_width=24),
        summary_panels,
        exception_panel,
        hotspot_panel,
        distribution_panel,
    )


def _status_text(message: str, color: str) -> Text:
    text = Text()
    text.append(message, style=color)
    return text


def _build_status_panel(title: str, border_style: str, lines: tuple[Text, ...]) -> Panel:
    return Panel(Group(*lines), title=title, border_style=border_style, padding=(1, 2))


def _metric_card(title: str, value: str, accent: str, caption: str) -> Panel:
    body = Group(
        Text(value, style=f"bold {accent}"),
        Text(caption, style="dim"),
    )
    return Panel(body, title=title, border_style=accent, padding=(0, 1), expand=True)


class _ResponsivePanelRow:
    """寬度足夠時以等寬欄排列面板，否則將面板逐一堆疊。"""

    _COLUMN_GAP = 1

    def __init__(self, panels: list[Panel], minimum_panel_width: int) -> None:
        self.panels = panels
        self.minimum_panel_width = minimum_panel_width

    def __rich_console__(self, console, options):
        minimum_width = (
            len(self.panels) * self.minimum_panel_width
            + max(0, len(self.panels) - 1) * 2 * self._COLUMN_GAP
        )
        if options.max_width >= minimum_width:
            row = Table.grid(expand=True, padding=(0, self._COLUMN_GAP))
            for _ in self.panels:
                row.add_column(ratio=1, min_width=self.minimum_panel_width)
            row.add_row(*self.panels)
            yield row
        else:
            yield Group(*self.panels, fit=False)


class _ProgressBar:
    """依照表格分配的欄寬繪製以最大筆數為基準的長條。"""

    def __init__(self, value: int, maximum: int) -> None:
        self.value = value
        self.maximum = maximum

    def __rich_console__(self, console, options):
        width = max(0, options.max_width)
        if width == 0:
            return
        filled = 0
        if self.maximum > 0 and self.value > 0:
            filled = min(width, max(1, round(self.value / self.maximum * width)))
        yield Text("█" * filled + "░" * (width - filled), style="green")


def _build_exception_summary_panel(summary: tuple[tuple[str, int, int], ...]) -> Panel:
    body = Table.grid(expand=True)
    body.add_column(style="bold red", ratio=2)
    body.add_column(justify="right", width=10)
    body.add_column(justify="right", width=10)

    if summary:
        for signature, group_count, occurrence_count in summary[:5]:
            body.add_row(signature, f"{group_count} 群組", f"{occurrence_count} 次")
        footer = Text(f"共 {len(summary)} 種例外群組", style="dim")
        return Panel(Group(body, footer), title="例外群組摘要", border_style="red", padding=(1, 2))

    return Panel(
        Group(
            Text("尚未偵測到例外群組", style="bold red"),
            Text("目前結果沒有可歸類的 stacktrace 或例外標題", style="dim"),
        ),
        title="例外群組摘要",
        border_style="red",
        padding=(1, 2),
    )


def _build_time_hotspot_panel(summary: tuple[tuple[str, int, int], ...]) -> Panel:
    body = Table.grid(expand=True)
    body.add_column(style="bold yellow", ratio=2)
    body.add_column(justify="right", width=10)
    body.add_column(justify="right", width=10)

    if summary:
        for bucket, group_count, occurrence_count in summary[:5]:
            body.add_row(bucket, f"{group_count} 群組", f"{occurrence_count} 次")
        footer = Text(f"共 {len(summary)} 個時段", style="dim")
        return Panel(Group(body, footer), title="時間熱點", border_style="yellow", padding=(1, 2))

    return Panel(
        Group(
            Text("尚未偵測到時間熱點", style="bold yellow"),
            Text("目前結果沒有可用的時間戳記", style="dim"),
        ),
        title="時間熱點",
        border_style="yellow",
        padding=(1, 2),
    )


def _build_logger_thread_distribution_panel(
    logger_summary: tuple[tuple[str, int, int], ...],
    thread_summary: tuple[tuple[str, int, int], ...],
) -> Panel:

    logger_table = _distribution_table("Logger", logger_summary, "blue")
    thread_table = _distribution_table("Thread", thread_summary, "magenta")

    if logger_summary or thread_summary:
        distribution_content = _ResponsivePanelRow(
            [logger_table, thread_table],
            minimum_panel_width=40,
        )
        return Panel(
            distribution_content,
            title="Logger / Thread 分布",
            border_style="blue",
            padding=(1, 2),
            expand=True,
        )

    return Panel(
        Group(
            Text("尚未偵測到 Logger / Thread 分布", style="bold blue"),
            Text("目前結果沒有足夠的 logger 或 thread 資訊", style="dim"),
        ),
        title="Logger / Thread 分布",
        border_style="blue",
        padding=(1, 2),
        expand=True,
    )


def _distribution_table(
    label: str,
    summary: tuple[tuple[str, int, int], ...],
    accent: str,
) -> Panel:
    body = Table.grid(expand=True)
    body.add_column(style=f"bold {accent}", ratio=2)
    body.add_column(justify="right", width=10)
    body.add_column(justify="right", width=10)

    if summary:
        for value, group_count, occurrence_count in summary[:5]:
            body.add_row(value, f"{group_count} 群組", f"{occurrence_count} 次")
        footer = Text(f"共 {len(summary)} 個 {label}", style="dim")
        return Panel(
            Group(body, footer),
            title=label,
            border_style=accent,
            padding=(1, 1),
            expand=True,
        )

    return Panel(
        Group(
            Text(f"尚無 {label}", style=f"bold {accent}"),
            Text("目前沒有可顯示的分布資訊", style="dim"),
        ),
        title=label,
        border_style=accent,
        padding=(1, 1),
        expand=True,
    )
