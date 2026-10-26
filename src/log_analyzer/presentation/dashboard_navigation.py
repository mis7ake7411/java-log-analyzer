"""摘要入口只保留瀏覽條件，不修改分析結果或產生新的分析請求。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DashboardTarget:
    """入口的顯示標籤與精確範圍；不保留群組內文或可變分析資料。"""

    label: str
    level: str | None = None
    group_index: int | None = None


def build_dashboard_targets(result, analysis) -> tuple[DashboardTarget, ...]:
    """將摘要轉成穩定排序的 Level 與單一事件入口。

    Args:
        result: 保留的分析結果。
        analysis: 與該結果對應的摘要資料。

    Returns:
        先 Level、後高頻事件的唯讀入口清單。
    """
    targets = [
        DashboardTarget(f"Level {level} · {count} 筆", level=str(level))
        for level, count in result.level_summary
        if count > 0
    ]
    targets.extend(
        DashboardTarget(
            f"高頻事件 · {event.count} 次 · {event.level} · {event.message}",
            group_index=event.group_index,
        )
        for event in getattr(analysis, "top_events", ())
    )
    return tuple(targets)
