from log_analyzer.application.analysis.service import AnalysisRequest, analyze_logs


def make_analysis_result(tmp_path):
    """由實際日誌建立結果，讓狀態／佈局測試也能觸發完整重繪。"""
    logs = tmp_path / "fixture-logs"
    logs.mkdir(exist_ok=True)
    (logs / "app.log").write_text(
        "2026-10-24 10:00:00.000 [main] ERROR Service - fixture event\n",
        encoding="utf-8",
    )
    return analyze_logs(AnalysisRequest(path=str(logs)))
