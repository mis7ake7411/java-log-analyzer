"""Physical log line reading, decoding, and scan coverage."""

from __future__ import annotations

import os
import re

from log_analyzer.domain.log_types import FileScanCoverage

from .entries import _STACKTRACE_HEADER_RE
from ..text_decoding import (
    SOURCE_TEXT_ENCODING,
    SOURCE_TEXT_ERRORS,
    SOURCE_TEXT_NEWLINE,
    normalize_decoded_line,
)

_POSSIBLE_LOG_HEADER_RE = re.compile(r"^\s*\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}")

def _record_line_coverage(coverage, line_num, line, matched, has_header):
    """將每行歸類到解析首行、空白、未知、堆疊或續行，保留少量未知樣本。

    原地更新 coverage 供零結果診斷使用；這項分類不負責判斷事件是否命中篩選。

    Args:
        coverage: 要原地累加的單檔 FileScanCoverage。
        line_num: 原始日誌從 1 起算的實體行號。
        line: 目前讀到的原文行，可能保留換行。
        matched: 此行是否符合事件首行格式。
        has_header: 本檔先前是否已出現符合格式的事件首行。

    Returns:
        None: 原地更新 coverage 的分類計數與樣本。
    """
    coverage.total_lines += 1
    stripped = line.strip()
    if matched:
        coverage.parsed_entries += 1
    elif not stripped:
        coverage.blank_lines += 1
    elif not has_header or _POSSIBLE_LOG_HEADER_RE.match(line):
        coverage.unrecognized_lines += 1
        if len(coverage.unrecognized_samples) < 3:
            coverage.unrecognized_samples.append((line_num, line.rstrip("\r\n")[:240]))
    elif stripped.startswith(("at ", "Caused by:", "Suppressed:", "... ")) or _STACKTRACE_HEADER_RE.match(stripped):
        coverage.stacktrace_lines += 1
    else:
        coverage.continuation_lines += 1


def iter_scanned_lines(
    filepath,
    entry_pattern,
    diagnostics=None,
    *,
    on_line=None,
    on_encoding_error=None,
):
    """讀取並解碼來源日誌，回傳每行的行號、文字與首行匹配結果。

    Args:
        filepath: 要逐行讀取的來源日誌路徑；涵蓋率會以路徑中的檔名記錄來源。
        entry_pattern: 已編譯的事件首行正則。
        diagnostics: 有提供時會累計單檔掃描涵蓋率。
        on_line: 每讀取一行時呼叫的無參數回呼；可省略。
        on_encoding_error: 每遇到解碼異常行時呼叫的回呼；可省略。

    Returns:
        Iterator[tuple[int, str, object | None]]: 行號、解碼後文字與首行匹配。

    Raises:
        OSError: 來源日誌無法開啟或讀取。
    """
    coverage = None
    if diagnostics is not None:
        coverage = FileScanCoverage(os.path.basename(filepath))
        diagnostics.file_coverage.append(coverage)
    has_header = False
    with open(
        filepath,
        "r",
        encoding=SOURCE_TEXT_ENCODING,
        errors=SOURCE_TEXT_ERRORS,
        newline=SOURCE_TEXT_NEWLINE,
    ) as file_handle:
        for line_num, line in enumerate(file_handle, 1):
            if on_line is not None:
                on_line()
            line, has_encoding_error = normalize_decoded_line(line)
            if has_encoding_error and on_encoding_error is not None:
                on_encoding_error()
            match = entry_pattern.match(line)
            if coverage is not None:
                _record_line_coverage(coverage, line_num, line, bool(match), has_header)

            if match:
                has_header = True
            yield line_num, line, match
