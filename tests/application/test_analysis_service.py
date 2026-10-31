from collections import Counter
from io import StringIO
from pathlib import Path
from typing import get_type_hints

import pytest
from rich.console import Console

import log_analyzer.application.analysis.service as analysis_service
from log_analyzer.application.analysis.service import (
    AnalysisRequest,
    AnalysisOptions,
    AnalysisResult,
    NoMatchingLogsError,
    ResultExportOptions,
    analyze_logs,
    export_analysis_result,
    run_analysis,
    run_analysis_with_options,
)
from log_analyzer.domain.log_types import MatchedLogs, ScanDiagnostics
from log_analyzer.infrastructure.exporting import ExportConflictError
from log_analyzer.presentation.tui.views import build_dashboard_view


def test_analysis_result_declares_matched_logs_contract():
    """情境：分析結果型別應明確宣告可迭代明細的契約。"""
    assert get_type_hints(AnalysisResult)["matched_logs"] is MatchedLogs


def test_analysis_result_defaults_scan_diagnostics():
    """情境：新結果未提供診斷時應建立獨立的預設掃描診斷。"""
    result = AnalysisResult(
        input_path="",
        output_path="",
        format_name="csv",
        keyword="未設定",
        ignore_case=False,
        total_logs=0,
        matched_groups=0,
        matched_occurrences=0,
        level_summary=[],
        selected_levels=(),
        sort_by="time",
        counts=Counter(),
        matched_logs=[],
        exported_files=[],
    )

    assert getattr(result, "diagnostics", None) == ScanDiagnostics()
    assert result.analysis_request is None


def test_export_preserves_replacement_for_invalid_utf8_source_bytes(tmp_path):
    """情境：來源含無效 UTF-8 時匯出仍應保留替代字元。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_bytes(
        b"2026-06-07 12:00:00.000 [main] ERROR com.test - damaged \x80 text\n"
    )
    result = analyze_logs(AnalysisRequest(path=str(logs), levels=None))

    try:
        assert result.diagnostics.encoding_error_files == 1
        assert result.diagnostics.encoding_error_lines == 1
        assert result.matched_logs[0]["message"] == "damaged \ufffd text"

        report_files = export_analysis_result(
            result,
            ResultExportOptions(
                output_path=str(tmp_path / "replacement.csv"),
                fields=("message",),
            ),
        )

        exported = Path(report_files[0]).read_text(encoding="utf-8-sig")
        assert "damaged \ufffd text" in exported
        assert "\x80" not in exported
    finally:
        result.matched_logs.release()


def test_analysis_options_pass_frozen_file_selection_to_analysis(tmp_path):
    """情境：分析設定中的固定檔名選擇應完整傳給分析服務。"""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "chosen.txt").write_text(
        "2026-06-07 12:00:00.000 INFO  [main] com.test.App: selected\n",
        encoding="utf-8",
    )
    (logs_dir / "sibling.out").write_text(
        "2026-06-07 12:00:01.000 ERROR [main] com.test.App: sibling\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "selected.csv"
    output_path.write_text("previous report", encoding="utf-8")
    options = AnalysisOptions(
        path=str(logs_dir),
        output_path=str(output_path),
        start_dt=None,
        end_dt=None,
        keyword=None,
        ignore_case=False,
        sort_by="time",
        fmt="csv",
        log_pattern="%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n",
        selected_files=("chosen.txt",),
        overwrite=True,
    )

    try:
        result = run_analysis_with_options(options)
    except TypeError as error:
        pytest.fail(f"analysis options should preserve selected files: {error}")

    try:
        assert result.counts == {"INFO": 1}
        assert result.analysis_request.selected_files == ("chosen.txt",)
        assert set(result.source_states) == {"chosen.txt"}
        assert output_path.read_text(encoding="utf-8-sig") != "previous report"
    finally:
        result.matched_logs.release()


def test_no_matches_carry_scan_diagnostics_and_real_scan_can_succeed_after_retry(tmp_path):
    """情境：零結果應攜帶掃描診斷且修正條件後可重新分析成功。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-06-07 12:00:00.000 [main] INFO  com.test.App - Started\n",
        encoding="utf-8",
    )

    with pytest.raises(NoMatchingLogsError) as caught:
        run_analysis(
            str(logs),
            str(tmp_path / "report.csv"),
            None,
            None,
            "not-present",
            False,
            "time",
            "csv",
        )

    assert str(caught.value).startswith("找不到符合條件的 log")
    from log_analyzer.domain.log_types import FileScanCoverage

    coverage = [FileScanCoverage("app.log", total_lines=1, parsed_entries=1)]
    assert caught.value.diagnostics == ScanDiagnostics(1, 1, 1, 1, 0, file_coverage=coverage)

    result = run_analysis(
        str(logs),
        str(tmp_path / "retry-report.csv"),
        None,
        None,
        None,
        False,
        "time",
        "csv",
    )

    assert result.total_logs == 1
    assert result.diagnostics == ScanDiagnostics(1, 1, 1, 1, 1, file_coverage=coverage)
    result.matched_logs.release()


def test_no_matches_release_empty_matched_store_before_raising(tmp_path, monkeypatch):
    """情境：零結果拋出例外前應釋放空的磁碟明細儲存器。"""
    class EmptyMatchedStore(list):
        diagnostics = ScanDiagnostics(1, 0, 0, 0, 0)

        def __init__(self):
            super().__init__()
            self.released = False

        def release(self):
            self.released = True

    matched_store = EmptyMatchedStore()
    monkeypatch.setattr(
        analysis_service,
        "parse_log_directory",
        lambda _path, _options: (Counter(), matched_store),
    )

    with pytest.raises(NoMatchingLogsError) as caught:
        run_analysis_with_options(
            AnalysisOptions(
                path=str(tmp_path),
                output_path=str(tmp_path / "report.csv"),
                start_dt=None,
                end_dt=None,
                keyword=None,
                ignore_case=False,
                sort_by="time",
                fmt="csv",
            )
        )

    assert caught.value.diagnostics is matched_store.diagnostics
    assert matched_store.released is True


def test_run_analysis_can_skip_returning_full_details(tmp_path):
    """情境：呼叫端不需要明細時應在匯出後釋放並回傳空集合。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-06-07 12:00:00.000 [main] INFO  com.test.App - First\n"
        "2026-06-07 12:01:00.000 [main] INFO  com.test.App - Second\n",
        encoding="utf-8",
    )

    result = run_analysis(
        str(logs),
        str(tmp_path / "report.csv"),
        None,
        None,
        None,
        False,
        "time",
        "csv",
        include_details=False,
    )

    assert result.exported_files
    assert result.matched_logs == []
    assert result.matched_occurrences == 2
    assert result.dashboard_summary is not None
    source_stat = (logs / "app.log").stat()
    assert result.source_states == {
        "app.log": {
            "size": source_stat.st_size,
            "mtime_ns": source_stat.st_mtime_ns,
            "stable": True,
        }
    }
    assert Path(result.output_path).exists()


def test_run_analysis_without_details_keeps_dashboard_summaries(tmp_path):
    """情境：不保留明細時仍應保存儀表板摘要。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-06-07 12:00:00.000 [worker-7] ERROR com.test.Worker - Request failed\n"
        "java.lang.IllegalStateException: boom\n",
        encoding="utf-8",
    )

    result = run_analysis(
        str(logs),
        str(tmp_path / "report.csv"),
        None,
        None,
        None,
        False,
        "time",
        "csv",
        include_details=False,
    )

    assert result.matched_logs == []
    assert result.matched_occurrences == 1
    rendered = StringIO()
    Console(file=rendered, width=140, color_system=None).print(
        build_dashboard_view(result, compact=True)
    )

    dashboard = rendered.getvalue()
    assert "java.lang.IllegalStateException: boom" in dashboard
    assert "2026-06-07 12:00" in dashboard
    assert "com.test.Worker" in dashboard
    assert "worker-7" in dashboard
    assert "1 次" in dashboard


def test_run_analysis_can_split_output_by_level(tmp_path):
    """情境：完整分析流程應支援依 Level 分檔匯出。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-06-07 12:00:00.000 [main] ERROR com.test.App - First\n"
        "2026-06-07 12:01:00.000 [main] WARN  com.test.App - Second\n",
        encoding="utf-8",
    )

    result = run_analysis(
        str(logs),
        str(tmp_path / "report.csv"),
        None,
        None,
        None,
        False,
        "time",
        "csv",
        include_details=False,
        levels=("ERROR", "WARN"),
        split_by_level=True,
    )

    assert result.output_path == str(tmp_path / "report_summary.csv")
    assert result.exported_files == [
        str(tmp_path / "report_summary.csv"),
        str(tmp_path / "report_ERROR.csv"),
        str(tmp_path / "report_WARN.csv"),
    ]


def test_run_analysis_with_options_matches_legacy_counts(tmp_path):
    """情境：新設定物件介面與舊參數介面應得到相同計數。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-06-07 12:00:00.000 [main] ERROR com.test.App - Failure\n"
        "2026-06-07 12:01:00.000 [main] INFO  com.test.App - Started\n",
        encoding="utf-8",
    )

    options_result = run_analysis_with_options(
        AnalysisOptions(
            path=str(logs),
            output_path=str(tmp_path / "options-report.csv"),
            start_dt=None,
            end_dt=None,
            keyword=None,
            ignore_case=False,
            sort_by="time",
            fmt="csv",
        )
    )
    legacy_result = run_analysis(
        str(logs),
        str(tmp_path / "legacy-report.csv"),
        None,
        None,
        None,
        False,
        "time",
        "csv",
    )

    assert options_result.counts == legacy_result.counts
    assert options_result.total_logs == legacy_result.total_logs
    assert options_result.matched_groups == legacy_result.matched_groups
    assert options_result.matched_occurrences == legacy_result.matched_occurrences


def test_legacy_run_analysis_accepts_keyword_only_overwrite(tmp_path):
    """情境：舊分析介面應接受具名 overwrite 參數。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(
        "2026-06-07 12:00:00.000 [main] INFO  com.test.App - Started\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.csv"
    output.write_text("previous report", encoding="utf-8")

    with pytest.raises(ExportConflictError):
        run_analysis(
            str(logs), str(output), None, None, None, False, "time", "csv"
        )

    result = run_analysis(
        str(logs), str(output), None, None, None, False, "time", "csv", overwrite=True
    )

    assert output.read_text(encoding="utf-8-sig") != "previous report"
    assert result.exported_files == [str(output.resolve())]


def test_export_analysis_result_never_overwrites_a_source_log_even_with_overwrite(tmp_path):
    """情境：即使授權覆蓋也不得將報表寫到來源日誌。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    source = logs / "app.log"
    source.write_text(
        "2026-06-07 12:00:00.000 [main] INFO  com.test.App - Started\n",
        encoding="utf-8",
    )
    result = analyze_logs(AnalysisRequest(path=str(logs)))
    original = source.read_bytes()
    result.output_path = "prior.csv"
    result.format_name = "md"
    result.exported_files = ["prior.md"]
    before = (result.output_path, result.format_name, list(result.exported_files))

    with pytest.raises(ValueError, match="來源"):
        export_analysis_result(
            result,
            ResultExportOptions(output_path=str(source), fmt="csv", overwrite=True),
        )

    assert source.read_bytes() == original
    assert (result.output_path, result.format_name, result.exported_files) == before
