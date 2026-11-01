"""以 Rich 組裝 Dashboard，可不啟動 Textual 應用程式而獨立繪製。"""

from rich.console import Group
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from log_analyzer.application.analysis.service import AnalysisResult
from log_analyzer.application.analysis.summary import DashboardAnalysis
from log_analyzer.domain.logback.pattern import DEFAULT_LOGBACK_PATTERN
from log_analyzer.presentation.analysis_labels import request_time_range
from log_analyzer.presentation.dashboard_navigation import build_dashboard_targets
from log_analyzer.presentation.scan_diagnostics import format_scan_coverage
from log_analyzer.presentation.tui.components.dashboard_card import dashboard_card
from log_analyzer.presentation.tui.dashboard_styles import (
    DASHBOARD_PALETTE,
    DashboardCardCategory,
)
from log_analyzer.presentation.encoding_warnings import format_encoding_warning


class DashboardView(Group):
    """以 Rich Group 組裝卡片，繪製時依可用寬度決定並排或堆疊。"""

    def __init__(
        self,
        result: AnalysisResult,
        analysis: DashboardAnalysis,
        *,
        navigation_generation: int | None = None,
    ) -> None:
        """將分析結果與摘要轉為顯示元件，不將來源物件保留為實例屬性。"""
        targets = (
            build_dashboard_targets(result, analysis)
            if navigation_generation is not None
            else ()
        )
        has_export = bool(result.exported_files)
        export_state = (
            f"已匯出 {result.format_name.upper()}" if has_export else "未匯出"
        )
        status_line = Text("分析完成", style=DASHBOARD_PALETTE.success_style)
        status_line.append(" · ")
        status_line.append(export_state, style=DASHBOARD_PALETTE.secondary_style)
        if navigation_generation is not None:
            status_line.append(" · d 摘要導覽", style=DASHBOARD_PALETTE.secondary_style)
        overview = dashboard_card(
            status_line,
            "執行狀態",
            padding=(0, 1),
            expand=True,
            category=DashboardCardCategory.SUMMARY,
        )
        exception_panel = _build_exception_summary_panel(analysis.exception_summary)
        hotspot_panel = _build_time_hotspot_panel(analysis.time_hotspots)
        distribution_panel = _build_logger_thread_distribution_panel(
            analysis.logger_summary,
            analysis.thread_summary,
        )

        metric_cards = [
            _metric_card("總 Log", str(result.total_logs), "整體筆數"),
            _metric_card("群組", str(result.matched_groups), "符合條件的事件群組"),
            _metric_card("命中", str(result.matched_occurrences), "條件觸發總次數"),
        ]

        metadata = Table.grid(padding=(0, 1), expand=True)
        metadata.add_column(style=DASHBOARD_PALETTE.secondary_bold_style, width=12)
        metadata.add_column(
            style=DASHBOARD_PALETTE.value_style,
            ratio=1,
            no_wrap=False,
            overflow="fold",
        )
        encoding_warning = format_encoding_warning(getattr(result, "diagnostics", None))
        if encoding_warning:
            metadata.add_row(
                "讀取提醒",
                Text(encoding_warning, style=DASHBOARD_PALETTE.warning_style),
            )
        analysis_request = getattr(result, "analysis_request", None)
        selected_path = getattr(analysis_request, "path", None)
        metadata.add_row(
            "Log 來源",
            Text(
                selected_path or result.input_path, style=DASHBOARD_PALETTE.value_style
            ),
        )
        if analysis_request is not None:
            metadata.add_row(
                "時間區間",
                Text(
                    request_time_range(analysis_request),
                    style=DASHBOARD_PALETTE.value_style,
                ),
            )
            metadata.add_row(
                "Pattern",
                Text(
                    getattr(analysis_request, "log_pattern", None)
                    or DEFAULT_LOGBACK_PATTERN,
                    style=DASHBOARD_PALETTE.value_style,
                ),
            )
        metadata.add_row(
            "輸出檔案",
            Text(
                "\n".join(result.exported_files) if has_export else "尚未匯出",
                style=DASHBOARD_PALETTE.value_style,
            ),
        )
        if len(result.exported_files) > 1:
            metadata.add_row("分割檔案", f"{len(result.exported_files)} 個")
        metadata.add_row(
            "關鍵字", Text(result.keyword, style=DASHBOARD_PALETTE.value_style)
        )
        levels = getattr(result, "selected_levels", ())
        selected_levels = ", ".join(levels) if levels else "全部"
        metadata.add_row(
            "Log 層級", Text(selected_levels, style=DASHBOARD_PALETTE.value_style)
        )
        metadata.add_row(
            "排序方式", "Level 分組" if result.sort_by == "level" else "時間排序"
        )
        metadata.add_row("忽略大小寫", "是" if result.ignore_case else "否")

        level_content = _build_level_distribution(
            result, targets, navigation_generation
        )

        summary_panel_items = [
            dashboard_card(
                metadata,
                "分析資訊",
                padding=(0, 1),
                expand=True,
                category=DashboardCardCategory.CONTEXT,
            ),
            dashboard_card(
                level_content,
                "Level 分布",
                padding=(0, 1),
                expand=True,
                category=DashboardCardCategory.DISTRIBUTION,
            ),
        ]
        summary_panels = Group(*summary_panel_items, fit=False)

        super().__init__(
            overview,
            _ResponsivePanelRow(metric_cards, minimum_panel_width=24),
            summary_panels,
            *(
                [
                    _build_top_events_panel(
                        analysis.top_events, targets, navigation_generation
                    )
                ]
                if getattr(analysis, "top_events", ())
                else []
            ),
            *(
                [
                    dashboard_card(
                        Text(coverage, style=DASHBOARD_PALETTE.secondary_style),
                        "逐檔解析診斷",
                        category=DashboardCardCategory.CONTEXT,
                    )
                ]
                if (
                    coverage := format_scan_coverage(
                        getattr(result, "diagnostics", None)
                    )
                )
                else []
            ),
            exception_panel,
            hotspot_panel,
            distribution_panel,
        )


def _metric_card(title: str, value: str, caption: str) -> Panel:
    body = Group(
        Text(value, style=DASHBOARD_PALETTE.value_bold_style),
        Text(caption, style=DASHBOARD_PALETTE.secondary_style),
    )
    return dashboard_card(
        body,
        title,
        padding=(0, 1),
        expand=True,
        category=DashboardCardCategory.SUMMARY,
    )


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

    def __init__(self, value: int, maximum: int, *, action: str | None = None) -> None:
        self.value = value
        self.maximum = maximum
        self.action = action

    def __rich_console__(self, console, options):
        width = max(0, options.max_width)
        if width == 0:
            return
        filled = 0
        if self.maximum > 0 and self.value > 0:
            filled = min(width, max(1, round(self.value / self.maximum * width)))
        yield Text(
            "█" * filled + "░" * (width - filled),
            style=_dashboard_link_style(self.action),
        )


def _dashboard_link_style(action: str | None) -> Style:
    """附上程式產生的入口編號，避免將日誌文字拼成操作指令。"""
    if action:
        return DASHBOARD_PALETTE.interactive_style(action)
    return DASHBOARD_PALETTE.value_style


def _build_level_distribution(result, targets, generation: int | None) -> Group:
    """以同一入口標記 Level 文字、筆數、比例及長條。"""
    indices = {
        target.level: index
        for index, target in enumerate(targets)
        if target.level is not None
    }
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style=DASHBOARD_PALETTE.value_style, width=7, no_wrap=True)
    table.add_column(
        style=DASHBOARD_PALETTE.value_style,
        justify="right",
        width=7,
        no_wrap=True,
    )
    table.add_column(
        style=DASHBOARD_PALETTE.value_style,
        justify="right",
        width=8,
        no_wrap=True,
    )
    table.add_column(ratio=1, min_width=1)
    table.add_row(
        Text("Level", style=DASHBOARD_PALETTE.secondary_style),
        Text("筆數", style=DASHBOARD_PALETTE.secondary_style),
        Text("占比", style=DASHBOARD_PALETTE.secondary_style),
        Text("相對最高", style=DASHBOARD_PALETTE.secondary_style),
    )
    if result.level_summary:
        maximum = max(count for _, count in result.level_summary)
        total = sum(count for _, count in result.level_summary)
        for level, count in result.level_summary:
            action = (
                f"app.open_dashboard_target({generation}, {indices[level]})"
                if level in indices
                else None
            )
            style = _dashboard_link_style(action)
            table.add_row(
                Text(str(level), style=style),
                Text(str(count), style=style),
                Text(f"{count / total * 100 if total else 0:.1f}%", style=style),
                _ProgressBar(count, maximum, action=action),
            )
    else:
        table.add_row(
            Text("N/A", style=DASHBOARD_PALETTE.secondary_style),
            Text("-", style=DASHBOARD_PALETTE.secondary_style),
            "",
            Text("沒有可顯示的統計", style=DASHBOARD_PALETTE.secondary_style),
        )
    return Group(
        table,
        Text(
            "占比以本次結果的總筆數計算；長條以最高筆數為滿格。",
            style=DASHBOARD_PALETTE.secondary_style,
        ),
    )


def _build_top_events_panel(events, targets, generation: int | None) -> Panel:
    """顯示已有摘要的前五個事件，以原群組索引連到精確的事件清單。"""
    indices = {
        target.group_index: index
        for index, target in enumerate(targets)
        if target.group_index is not None
    }
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(ratio=1, overflow="fold")
    table.add_column(width=10, justify="right")
    for event in events:
        action = (
            f"app.open_dashboard_target({generation}, {indices[event.group_index]})"
            if event.group_index in indices
            else None
        )
        style = _dashboard_link_style(action)
        table.add_row(
            Text(f"{event.level} · {event.message}", style=style),
            Text(f"{event.count} 次", style=style),
        )
    return dashboard_card(
        table,
        "高頻事件（前 5 項）",
        padding=(0, 1),
        expand=True,
        category=DashboardCardCategory.EVENTS,
    )


def _build_exception_summary_panel(summary: tuple[tuple[str, int, int], ...]) -> Panel:
    body = Table.grid(expand=True)
    body.add_column(style=DASHBOARD_PALETTE.error_style, ratio=2)
    body.add_column(style=DASHBOARD_PALETTE.value_style, justify="right", width=10)
    body.add_column(style=DASHBOARD_PALETTE.value_style, justify="right", width=10)

    if summary:
        for signature, group_count, occurrence_count in summary[:5]:
            body.add_row(signature, f"{group_count} 群組", f"{occurrence_count} 次")
        footer = Text(
            f"共 {len(summary)} 種例外群組",
            style=DASHBOARD_PALETTE.secondary_style,
        )
        return dashboard_card(
            Group(body, footer),
            "例外群組摘要",
            padding=(1, 2),
            category=DashboardCardCategory.EVENTS,
        )

    return dashboard_card(
        Group(
            Text("尚未偵測到例外群組", style=DASHBOARD_PALETTE.secondary_bold_style),
            Text(
                "目前結果沒有可歸類的 stacktrace 或例外標題",
                style=DASHBOARD_PALETTE.secondary_style,
            ),
        ),
        "例外群組摘要",
        padding=(1, 2),
        category=DashboardCardCategory.EVENTS,
    )


def _build_time_hotspot_panel(summary: tuple[tuple[str, int, int], ...]) -> Panel:
    body = Table.grid(expand=True)
    body.add_column(style=DASHBOARD_PALETTE.value_bold_style, ratio=2)
    body.add_column(style=DASHBOARD_PALETTE.value_style, justify="right", width=10)
    body.add_column(style=DASHBOARD_PALETTE.value_style, justify="right", width=10)

    if summary:
        for bucket, group_count, occurrence_count in summary[:5]:
            body.add_row(bucket, f"{group_count} 群組", f"{occurrence_count} 次")
        footer = Text(
            f"共 {len(summary)} 個時段",
            style=DASHBOARD_PALETTE.secondary_style,
        )
        return dashboard_card(
            Group(body, footer),
            "時間熱點",
            padding=(1, 2),
            category=DashboardCardCategory.EVENTS,
        )

    return dashboard_card(
        Group(
            Text("尚未偵測到時間熱點", style=DASHBOARD_PALETTE.secondary_bold_style),
            Text("目前結果沒有可用的時間戳記", style=DASHBOARD_PALETTE.secondary_style),
        ),
        "時間熱點",
        padding=(1, 2),
        category=DashboardCardCategory.EVENTS,
    )


def _build_logger_thread_distribution_panel(
    logger_summary: tuple[tuple[str, int, int], ...],
    thread_summary: tuple[tuple[str, int, int], ...],
) -> Panel:

    logger_table = _distribution_table("Logger", logger_summary)
    thread_table = _distribution_table("Thread", thread_summary)

    if logger_summary or thread_summary:
        distribution_content = _ResponsivePanelRow(
            [logger_table, thread_table],
            minimum_panel_width=40,
        )
        return dashboard_card(
            distribution_content,
            "Logger / Thread 分布",
            padding=(1, 2),
            expand=True,
            category=DashboardCardCategory.DISTRIBUTION,
        )

    return dashboard_card(
        Group(
            Text(
                "尚未偵測到 Logger / Thread 分布",
                style=DASHBOARD_PALETTE.secondary_bold_style,
            ),
            Text(
                "目前結果沒有足夠的 logger 或 thread 資訊",
                style=DASHBOARD_PALETTE.secondary_style,
            ),
        ),
        "Logger / Thread 分布",
        padding=(1, 2),
        expand=True,
        category=DashboardCardCategory.DISTRIBUTION,
    )


def _distribution_table(
    label: str,
    summary: tuple[tuple[str, int, int], ...],
) -> Panel:
    body = Table.grid(expand=True)
    body.add_column(style=DASHBOARD_PALETTE.value_bold_style, ratio=2)
    body.add_column(style=DASHBOARD_PALETTE.value_style, justify="right", width=10)
    body.add_column(style=DASHBOARD_PALETTE.value_style, justify="right", width=10)

    if summary:
        for value, group_count, occurrence_count in summary[:5]:
            body.add_row(value, f"{group_count} 群組", f"{occurrence_count} 次")
        footer = Text(
            f"共 {len(summary)} 個 {label}",
            style=DASHBOARD_PALETTE.secondary_style,
        )
        return dashboard_card(
            Group(body, footer),
            label,
            padding=(1, 1),
            expand=True,
            category=DashboardCardCategory.DISTRIBUTION,
        )

    return dashboard_card(
        Group(
            Text(f"尚無 {label}", style=DASHBOARD_PALETTE.secondary_bold_style),
            Text("目前沒有可顯示的分布資訊", style=DASHBOARD_PALETTE.secondary_style),
        ),
        label,
        padding=(1, 1),
        expand=True,
        category=DashboardCardCategory.DISTRIBUTION,
    )
