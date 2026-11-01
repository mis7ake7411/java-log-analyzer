from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from log_analyzer.presentation.tui.components.dashboard import DashboardView
from log_analyzer.presentation.tui.components.dashboard_card import dashboard_card
from log_analyzer.presentation.tui.dashboard_styles import (
    DASHBOARD_PALETTE,
    DashboardCardCategory,
)
from ...application.analysis.service import AnalysisResult
from ...application.analysis.summary import DashboardAnalysis, aggregate_dashboard_summary
from ...domain.log_types import ScanDiagnostics
from ..encoding_warnings import format_encoding_warning
from log_analyzer.presentation.scan_diagnostics import format_scan_coverage
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
        (
            Text("尚未產生分析結果", style=DASHBOARD_PALETTE.secondary_bold_style),
            Text(
                "請先在上方填入條件，然後按「開始分析」",
                style=DASHBOARD_PALETTE.value_style,
            ),
            Text(
                "Enter 開始分析，c 清除結果，q 離開",
                style=DASHBOARD_PALETTE.secondary_style,
            ),
        ),
    )


def build_loading_view() -> Panel:
    return _build_status_panel(
        "執行中",
        (
            Text("分析中，請稍候...", style=DASHBOARD_PALETTE.secondary_bold_style),
            Text(
                "正在掃描 Log 目錄並套用條件",
                style=DASHBOARD_PALETTE.value_style,
            ),
            Text("大型資料夾可能需要較久時間", style=DASHBOARD_PALETTE.secondary_style),
        ),
    )


def build_error_view(title: str, message: str) -> Panel:
    return _build_status_panel(
        "執行失敗",
        (
            Text(title, style=DASHBOARD_PALETTE.error_style),
            Text(message, style=DASHBOARD_PALETTE.value_style),
            Text(
                get_error_hint(title, message),
                style=DASHBOARD_PALETTE.secondary_style,
            ),
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
        (
            Text("沒有符合資料", style=DASHBOARD_PALETTE.warning_style),
            Text(reason, style=DASHBOARD_PALETTE.value_style),
            Text(
                f"掃描 Log 檔案：{diagnostics.files_scanned}",
                style=DASHBOARD_PALETTE.value_style,
            ),
            Text(
                f"符合 Pattern 的事件：{diagnostics.parsed_entries}",
                style=DASHBOARD_PALETTE.value_style,
            ),
            Text(f"時間範圍後：{diagnostics.after_time}", style=DASHBOARD_PALETTE.value_style),
            Text(f"Level 後：{diagnostics.after_level}", style=DASHBOARD_PALETTE.value_style),
            Text(f"關鍵字後：{diagnostics.after_keyword}", style=DASHBOARD_PALETTE.value_style),
        ),
    )
    encoding_warning = format_encoding_warning(diagnostics)
    if encoding_warning:
        panel.renderable = Group(
            panel.renderable,
            Text(encoding_warning, style=DASHBOARD_PALETTE.warning_style),
        )
    coverage = format_scan_coverage(diagnostics)
    if coverage:
        panel.renderable = Group(
            panel.renderable,
            Text(coverage, style=DASHBOARD_PALETTE.secondary_style),
        )
    return panel


def format_path_status(message: str, color: str, require_writable: bool) -> Text:
    if color == "green":
        label = "可寫" if require_writable else "可讀"
        return _status_text(f"狀態：{label}", color)
    return _status_text(message, color)


def build_dashboard_view(
    result: AnalysisResult,
    compact: bool,
    *,
    navigation_generation: int | None = None,
) -> Group:
    """Build the legacy Rich dashboard API; compact remains compatibility-only.

    DashboardView lays out rows from the active console width.
    """
    analysis = analyze_dashboard(result)
    return DashboardView(
        result,
        analysis,
        navigation_generation=navigation_generation,
    )


def _status_text(message: str, color: str) -> Text:
    text = Text()
    text.append(message, style=color)
    return text


def _build_status_panel(title: str, lines: tuple[Text, ...]) -> Panel:
    return dashboard_card(
        Group(*lines),
        title,
        padding=(1, 2),
        category=DashboardCardCategory.SUMMARY,
    )
