from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..application.analysis_service import AnalysisResult


def build_idle_view() -> Panel:
    body = Text()
    body.append("尚未產生分析結果\n", style="bold cyan")
    body.append("\n")
    body.append("請先在上方填入條件，然後按「開始分析」。\n", style="white")
    body.append("\n")
    body.append("Enter 開始分析，c 清除結果，q 離開。", style="dim")
    return Panel(body, title="結果區", border_style="cyan", padding=(1, 2))


def build_loading_view() -> Panel:
    body = Text("分析中，請稍候...", style="bold cyan")
    return Panel(body, title="執行中", border_style="cyan", padding=(1, 2))


def build_error_view(title: str, message: str) -> Panel:
    body = Text()
    body.append(f"{title}\n", style="bold red")
    body.append(message, style="white")
    return Panel(body, title="執行失敗", border_style="red", padding=(1, 2))


def format_path_status(message: str, color: str, require_writable: bool) -> Text:
    if color == "green":
        label = "可寫" if require_writable else "可讀"
        return _status_text(f"狀態：{label}", color)
    return _status_text(message, color)


def build_dashboard_view(result: AnalysisResult, compact: bool) -> Group:
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


def _status_text(message: str, color: str) -> Text:
    text = Text()
    text.append(message, style=color)
    return text


def _metric_card(title: str, value: str, accent: str, caption: str) -> Panel:
    body = Group(
        Text(value, style=f"bold {accent}"),
        Text(caption, style="dim"),
    )
    return Panel(body, title=title, border_style=accent, padding=(1, 2))


def _progress_bar(value: int, maximum: int, width: int = 18) -> str:
    if maximum <= 0:
        return "░" * width
    filled = max(1, round((value / maximum) * width))
    filled = min(filled, width)
    return "█" * filled + "░" * (width - filled)
