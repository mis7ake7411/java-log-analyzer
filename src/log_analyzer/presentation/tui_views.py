import re
from collections import Counter
from datetime import datetime

from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..application.analysis_service import AnalysisResult
from .error_messages import get_error_hint


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
            Text("正在掃描 Log 目錄、套用條件並產生輸出", style="white"),
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


def format_path_status(message: str, color: str, require_writable: bool) -> Text:
    if color == "green":
        label = "可寫" if require_writable else "可讀"
        return _status_text(f"狀態：{label}", color)
    return _status_text(message, color)


def build_dashboard_view(result: AnalysisResult, compact: bool) -> Group:
    overview = Panel(
        Group(
            Text("分析完成", style="bold green"),
            Text("先看摘要，再往下看細節", style="dim"),
        ),
        border_style="green",
        padding=(1, 2),
        title="執行狀態",
    )
    exception_panel = _build_exception_summary_panel(result)
    hotspot_panel = _build_time_hotspot_panel(result)
    distribution_panel = _build_logger_thread_distribution_panel(result)

    metric_cards = [
        _metric_card("總 Log", str(result.total_logs), "cyan", "整體筆數"),
        _metric_card("群組", str(result.matched_groups), "green", "符合條件的事件群組"),
        _metric_card("命中", str(result.matched_occurrences), "yellow", "條件觸發總次數"),
        _metric_card("格式", result.format_name.upper(), "magenta", "輸出檔案格式"),
    ]

    metadata = Table.grid(padding=(0, 1))
    metadata.add_column(style="bold cyan", width=12)
    metadata.add_column(ratio=1)
    metadata.add_row("輸入目錄", result.input_path)
    metadata.add_row("輸出檔案", result.output_path)
    if len(result.exported_files) > 1:
        metadata.add_row("分割檔案", f"{len(result.exported_files)} 個")
    metadata.add_row("關鍵字", result.keyword)
    metadata.add_row("排序方式", "Level 分組" if result.sort_by == "level" else "時間排序")
    metadata.add_row("忽略大小寫", "是" if result.ignore_case else "否")

    level_table = Table.grid(expand=True)
    level_table.add_column(style="bold", width=12)
    level_table.add_column(justify="right", width=8)
    level_table.add_column(ratio=1)
    if result.level_summary:
        max_count = max(count for _, count in result.level_summary)
        for level, count in result.level_summary:
            level_table.add_row(level, str(count), _progress_bar(count, max_count))
    else:
        level_table.add_row("N/A", "-", "沒有可顯示的統計")

    summary_panels = Columns(
        [
            Panel(metadata, title="分析資訊", border_style="blue", padding=(1, 2)),
            Panel(level_table, title="Level 分布", border_style="green", padding=(1, 2)),
        ],
        equal=True,
        expand=True,
    )

    if compact:
        return Group(
            overview,
            *metric_cards,
            summary_panels,
            exception_panel,
            hotspot_panel,
            distribution_panel,
        )

    return Group(
        overview,
        Columns(metric_cards, equal=True, expand=True),
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
    return Panel(body, title=title, border_style=accent, padding=(1, 2))


def _build_exception_summary_panel(result: AnalysisResult) -> Panel:
    summary = _collect_exception_summary(result.matched_logs)
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


def _build_time_hotspot_panel(result: AnalysisResult) -> Panel:
    summary = _collect_time_hotspots(result.matched_logs)
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


def _build_logger_thread_distribution_panel(result: AnalysisResult) -> Panel:
    logger_summary = _collect_distribution(result.matched_logs, "logger")
    thread_summary = _collect_distribution(result.matched_logs, "thread")

    logger_table = _distribution_table("Logger", logger_summary, "blue")
    thread_table = _distribution_table("Thread", thread_summary, "magenta")

    if logger_summary or thread_summary:
        return Panel(
            Columns([logger_table, thread_table], equal=True, expand=True),
            title="Logger / Thread 分布",
            border_style="blue",
            padding=(1, 2),
        )

    return Panel(
        Group(
            Text("尚未偵測到 Logger / Thread 分布", style="bold blue"),
            Text("目前結果沒有足夠的 logger 或 thread 資訊", style="dim"),
        ),
        title="Logger / Thread 分布",
        border_style="blue",
        padding=(1, 2),
    )


def _distribution_table(label: str, summary: list[tuple[str, int, int]], accent: str) -> Panel:
    body = Table.grid(expand=True)
    body.add_column(style=f"bold {accent}", ratio=2)
    body.add_column(justify="right", width=10)
    body.add_column(justify="right", width=10)

    if summary:
        for value, group_count, occurrence_count in summary[:5]:
            body.add_row(value, f"{group_count} 群組", f"{occurrence_count} 次")
        footer = Text(f"共 {len(summary)} 個 {label}", style="dim")
        return Panel(Group(body, footer), title=label, border_style=accent, padding=(1, 1))

    return Panel(
        Group(
            Text(f"尚無 {label}", style=f"bold {accent}"),
            Text("目前沒有可顯示的分布資訊", style="dim"),
        ),
        title=label,
        border_style=accent,
        padding=(1, 1),
    )


def _collect_distribution(matched_logs, field: str) -> list[tuple[str, int, int]]:
    grouped = Counter()
    group_counts = Counter()

    for log in matched_logs or []:
        value = str(log.get(field, "") or "").strip()
        if not value:
            continue
        occurrence_count = int(log.get("count", 1))
        grouped[value] += occurrence_count
        group_counts[value] += 1

    summary = [
        (value, group_counts[value], grouped[value])
        for value in grouped
    ]
    summary.sort(key=lambda item: (-item[2], -item[1], item[0]))
    return summary


def _collect_time_hotspots(matched_logs) -> list[tuple[str, int, int]]:
    grouped = Counter()
    group_counts = Counter()

    for log in matched_logs or []:
        bucket = _extract_time_bucket(log)
        if not bucket:
            continue
        occurrence_count = int(log.get("count", 1))
        grouped[bucket] += occurrence_count
        group_counts[bucket] += 1

    summary = [
        (bucket, group_counts[bucket], grouped[bucket])
        for bucket in grouped
    ]
    summary.sort(key=lambda item: (-item[2], -item[1], item[0]))
    return summary


def _extract_time_bucket(log) -> str:
    timestamp = str(log.get("timestamp", "") or "").strip()
    if not timestamp:
        return ""

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(timestamp[:26], fmt)
            return parsed.strftime("%Y-%m-%d %H:00")
        except ValueError:
            continue
    return timestamp[:13] + ":00" if len(timestamp) >= 13 else ""


def _collect_exception_summary(matched_logs) -> list[tuple[str, int, int]]:
    grouped = Counter()
    group_counts = Counter()

    for log in matched_logs or []:
        signature = _extract_exception_signature(log)
        if not signature:
            continue
        occurrence_count = int(log.get("count", 1))
        grouped[signature] += occurrence_count
        group_counts[signature] += 1

    summary = [
        (signature, group_counts[signature], grouped[signature])
        for signature in grouped
    ]
    summary.sort(key=lambda item: (-item[2], -item[1], item[0]))
    return summary


def _extract_exception_signature(log) -> str:
    stacktrace = str(log.get("stacktrace", "") or "").strip()
    if stacktrace:
        first_line = stacktrace.splitlines()[0]
        cleaned = re.sub(r"^\s*\d+:\s*", "", first_line).strip()
        if cleaned:
            return cleaned

    message_body = str(log.get("message_body", "") or "").strip()
    if message_body:
        for line in message_body.splitlines():
            cleaned = line.strip()
            if cleaned and ("Exception" in cleaned or "Error" in cleaned or cleaned.startswith("Caused by:")):
                return cleaned

    message = str(log.get("message", "") or "").strip()
    if "Exception" in message or "Error" in message:
        return message.splitlines()[0].strip()

    return ""


def _progress_bar(value: int, maximum: int, width: int = 18) -> str:
    if maximum <= 0:
        return "░" * width
    filled = max(1, round((value / maximum) * width))
    filled = min(filled, width)
    return "█" * filled + "░" * (width - filled)
