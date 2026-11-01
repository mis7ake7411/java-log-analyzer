"""從固定快照還原事件原文，不重新開啟追蹤來源。"""

from collections.abc import Mapping

from log_analyzer.application.live.session import TrackingSnapshot
from log_analyzer.application.sources.preview import SourcePreview


def read_tracking_context(snapshot: TrackingSnapshot, occurrence: Mapping) -> SourcePreview:
    """核對接收序號與來源世代，讀取快照中保留的完整事件。

    Args:
        snapshot: 顯示與匯出共同持有的固定截止點快照。
        occurrence: 包含 sequence 與 generation 的追蹤事件座標。

    Returns:
        SourcePreview: 事件自身的原文與實體行號；無效座標或讀取失敗時附帶提示。
            不包含事件以外的前後行，也不以目前來源檔補齊。
    """
    sequence = occurrence.get("sequence")
    generation = occurrence.get("generation")
    unavailable = SourcePreview(
        path_line="", lines=(), match_line=0,
        warning="此事件不在目前追蹤快照內，未讀取原始來源檔。",
    )
    if (
        type(sequence) is not int
        or type(generation) is not int
        or sequence < 1
        or sequence > snapshot.cutoff
        or generation < 0
    ):
        return unavailable
    try:
        entries = list(snapshot.events.iter_page(sequence - 1, 1))
    except OSError as exc:
        return SourcePreview(
            path_line="", lines=(), match_line=0,
            warning=f"無法讀取已保留的追蹤原文：{exc}",
        )
    if not entries:
        return unavailable
    event = entries[0]
    if event.get("sequence") != sequence or event.get("generation") != generation:
        return unavailable
    first_line = event["line_num"]
    return SourcePreview(
        path_line=f"{event['filename']}:{first_line}（已保留原文；世代 {generation}，序號 {sequence}）",
        lines=tuple(enumerate(event.get("raw_text", "").splitlines(), start=first_line)),
        match_line=first_line,
    )
