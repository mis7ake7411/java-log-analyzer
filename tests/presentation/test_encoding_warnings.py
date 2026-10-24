from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.presentation.encoding_warnings import format_encoding_warning


def test_encoding_warning_includes_file_and_line_counts_and_replacement_character():
    """情境：編碼警告應列出異常檔數、行數及替代字元。"""
    warning = format_encoding_warning(ScanDiagnostics(2, 4, 4, 4, 4, 1, 3))

    assert warning is not None
    assert "UTF-8" in warning
    assert "1 個檔案" in warning
    assert "3 行" in warning
    assert "�" in warning


def test_encoding_warning_is_absent_without_loss_diagnostics():
    """情境：沒有解碼損失診斷時不應顯示警告。"""
    assert format_encoding_warning(None) is None
    assert format_encoding_warning(ScanDiagnostics()) is None
