from pathlib import Path

from log_analyzer.application.analysis_service import run_analysis


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
