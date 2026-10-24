from __future__ import annotations
from log_analyzer.domain.logback_pattern import DEFAULT_LOGBACK_PATTERN


def request_time_range(request) -> str:
    if (
        request is None
        or not hasattr(request, "start_dt")
        or not hasattr(request, "end_dt")
    ):
        return "未記錄"
    start, end = getattr(request, "start_dt", None), getattr(request, "end_dt", None)
    return f"{start.isoformat(sep=' ') if start else '不限開始'} → {end.isoformat(sep=' ') if end else '不限結束'}"


def export_result_context(result) -> str:
    request = getattr(result, "analysis_request", None)
    source = getattr(request, "path", None) or getattr(
        result, "input_path", "來源未記錄"
    )
    if request is None:
        return f"匯出目前保留結果（原條件）\n來源：{source}\n分析條件未記錄。"
    pattern = getattr(request, "log_pattern", None) or DEFAULT_LOGBACK_PATTERN
    keyword = getattr(request, "keyword", None) or "未設定"
    return (
        f"匯出目前保留結果（原條件）\n來源：{source}\n"
        f"時間區間：{request_time_range(request)}\n關鍵字：{keyword}\nPattern：{pattern}"
    )
