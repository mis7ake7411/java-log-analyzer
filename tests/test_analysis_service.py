from pathlib import Path
from typing import get_type_hints

from log_analyzer.application.analysis_service import (
    AnalysisOptions,
    AnalysisResult,
    run_analysis,
    run_analysis_with_options,
)
from log_analyzer.domain.log_types import MatchedLogs


def test_analysis_result_declares_matched_logs_contract():
    assert get_type_hints(AnalysisResult)["matched_logs"] is MatchedLogs


def test_run_analysis_can_skip_returning_full_details(tmp_path):
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
    assert Path(result.output_path).exists()


def test_run_analysis_can_split_output_by_level(tmp_path):
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
