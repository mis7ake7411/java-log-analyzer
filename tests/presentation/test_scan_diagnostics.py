from log_analyzer.domain.log_types import FileScanCoverage, ScanDiagnostics
from log_analyzer.presentation.scan_diagnostics import (
    format_scan_coverage,
    iter_scan_coverage_parts,
)


def test_coverage_summary_keeps_literal_samples_and_marks_truncated_file_list():
    """情境：覆蓋摘要應原樣顯示樣本並標明檔案清單截斷。"""
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
    """情境：舊版診斷缺少覆蓋資料時不得宣稱已核對覆蓋率。"""
    assert format_scan_coverage(None) is None
    assert format_scan_coverage(ScanDiagnostics()) is None


def test_coverage_parts_preserve_formatter_text_and_assign_explicit_roles():
    diagnostics = ScanDiagnostics(
        file_coverage=[
            FileScanCoverage(
                "service12.log",
                total_lines=5,
                parsed_entries=3,
                blank_lines=1,
                unrecognized_lines=1,
                unrecognized_samples=[(5, "INFO 2031 [red] literal")],
            ),
            FileScanCoverage(
                "clean9.log",
                total_lines=2,
                parsed_entries=2,
                unrecognized_lines=0,
            ),
        ]
    )
    parts = list(iter_scan_coverage_parts(diagnostics))

    expected = (
        "掃描 2 個檔案、7 個實體行；未辨識／疑似不同格式 1 行。"
        "\nservice12.log：事件 3；堆疊 0；訊息續行 0；空行 1；未辨識 1"
        "\n  第 5 行：INFO 2031 [red] literal"
        "\nclean9.log：事件 2；堆疊 0；訊息續行 0；空行 0；未辨識 0"
        "\n分類在篩選前計算；續行採啟發式辨識，不能作為完整解析成功率。"
    )
    assert "".join(text for text, _role in parts) == expected
    assert format_scan_coverage(diagnostics) == expected
    assert ("service12.log", "identifier") in parts
    assert ("clean9.log", "identifier") in parts
    assert ("1", "warning") in parts
    assert ("0", "value") in parts
    assert ("5", "value") in parts
    assert ("INFO 2031 [red] literal", "value") in parts
    assert ("；堆疊 ", "secondary") in parts
    assert ("分類在篩選前計算；續行採啟發式辨識，不能作為完整解析成功率。", "secondary") in parts


def test_coverage_parts_are_empty_when_coverage_is_unavailable():
    assert list(iter_scan_coverage_parts(None)) == []
    assert list(iter_scan_coverage_parts(ScanDiagnostics())) == []
