"""跨檔解析、關聯查詢、來源核對與匯出使用相同的逐筆 MDC。"""

import asyncio
import json
from pathlib import Path

from log_analyzer.application.analysis_service import (
    AnalysisRequest,
    ResultExportOptions,
    analyze_logs,
    export_analysis_bundle,
)
from log_analyzer.application.event_correlation import find_correlated_occurrences
from log_analyzer.application.source_preview import read_source_context


def test_retained_mdc_correlates_across_files_and_survives_export(tmp_path):
    """情境：同訊息的不同請求合併後仍可精確追查，原文與匯出不丟失 MDC。"""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.log").write_text(
        "2026-10-31 12:03:00.000 [worker] ERROR App [Trace-A] failed\n"
        "2026-10-31 12:04:00.000 [worker] ERROR App [Trace-B] failed\n",
        encoding="utf-8",
    )
    (source / "b.log").write_text(
        "2026-10-31 12:01:00.000 [worker] INFO App [Trace-A] started\n"
        "2026-10-31 12:02:00.000 [worker] ERROR App [Trace-A] failed\n",
        encoding="utf-8",
    )
    result = analyze_logs(AnalysisRequest(
        path=str(source),
        log_pattern="%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %level %logger [%X{traceId}] %msg%n",
    ))
    try:
        assert (result.matched_groups, result.matched_occurrences) == (2, 4)
        matches = asyncio.run(find_correlated_occurrences(result.matched_logs, "traceId", "Trace-A"))
        assert [(row.filename, row.line_number) for row in matches] == [
            ("b.log", 1), ("b.log", 2), ("a.log", 1),
        ]
        first = matches[0]
        group = result.matched_logs[first.group_index]
        occurrence = group["occurrences"][first.occurrence_index]
        assert occurrence["mdc"] == {"traceId": "Trace-A"}
        preview = read_source_context(
            result.input_path, occurrence,
            result.matched_logs.source_states[first.filename],
        )
        assert preview.warning is None
        assert preview.match_line == 1
        assert "[Trace-A] started" in dict(preview.lines)[1]

        reports, _ = export_analysis_bundle(result, ResultExportOptions(
            output_path=str(tmp_path / "report.json"), fmt="json",
        ))
        exported = json.loads(Path(reports[0]).read_text(encoding="utf-8"))
        traces = [
            item["mdc"]["traceId"]
            for entry in exported["results"]
            for item in entry["occurrences"]
        ]
        assert sorted(traces) == ["Trace-A", "Trace-A", "Trace-A", "Trace-B"]
        assert (result.matched_groups, result.matched_occurrences) == (2, 4)
    finally:
        result.matched_logs.release()
