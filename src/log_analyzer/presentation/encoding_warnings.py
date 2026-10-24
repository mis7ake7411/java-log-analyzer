from __future__ import annotations

from ..domain.log_types import ScanDiagnostics


def format_encoding_warning(diagnostics: ScanDiagnostics | None) -> str | None:
    """Format the shared nonfatal notice for UTF-8 replacement diagnostics."""
    if diagnostics is None:
        return None

    error_files = getattr(diagnostics, "encoding_error_files", 0)
    error_lines = getattr(diagnostics, "encoding_error_lines", 0)
    if not isinstance(error_files, int) or isinstance(error_files, bool) or error_files < 0:
        error_files = 0
    if not isinstance(error_lines, int) or isinstance(error_lines, bool) or error_lines < 0:
        error_lines = 0
    if error_files == 0 and error_lines == 0:
        return None

    return (
        f"UTF-8 編碼提醒：{error_files} 個檔案、{error_lines} 行含無效 UTF-8 位元組，"
        "顯示與匯出以 � 取代；如需保留來源字元，請確認檔案編碼後再轉成 UTF-8。"
    )
