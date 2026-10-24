from log_analyzer.domain.log_types import FileScanCoverage, ScanDiagnostics
from log_analyzer.presentation.scan_diagnostics import format_scan_coverage


def test_coverage_summary_keeps_literal_samples_and_marks_truncated_file_list():
    files = [FileScanCoverage(f"{i}.log", total_lines=2, parsed_entries=1) for i in range(21)]
    files[0].unrecognized_lines = 1
    files[0].unrecognized_samples = [(2, "[red]literal[/red]")]
    text = format_scan_coverage(ScanDiagnostics(file_coverage=files))
    assert "21 個檔案、42 個實體行" in text
    assert "未辨識／疑似不同格式 1 行" in text
    assert "第 2 行：[red]literal[/red]" in text
    assert "另 1 個檔案" in text
    assert "不能作為完整解析成功率" in text


def test_legacy_diagnostics_do_not_claim_coverage():
    assert format_scan_coverage(None) is None
    assert format_scan_coverage(ScanDiagnostics()) is None
