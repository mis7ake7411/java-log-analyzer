from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.presentation.encoding_warnings import format_encoding_warning


def test_encoding_warning_includes_file_and_line_counts_and_replacement_character():
    warning = format_encoding_warning(ScanDiagnostics(2, 4, 4, 4, 4, 1, 3))

    assert warning is not None
    assert "UTF-8" in warning
    assert "1 個檔案" in warning
    assert "3 行" in warning
    assert "�" in warning


def test_encoding_warning_is_absent_without_loss_diagnostics():
    assert format_encoding_warning(None) is None
    assert format_encoding_warning(ScanDiagnostics()) is None
