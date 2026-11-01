from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from log_analyzer.domain.log_types import ScanDiagnostics


ScanCoverageRole = Literal["secondary", "value", "identifier", "warning"]
ScanCoveragePart = tuple[str, ScanCoverageRole]


def iter_scan_coverage_parts(
    diagnostics: ScanDiagnostics | None,
) -> Iterator[ScanCoveragePart]:
    """逐段回傳掃描覆蓋診斷文字及語意角色，不依賴呈現框架。"""
    files = getattr(diagnostics, "file_coverage", ())
    if not files:
        return

    total = sum(item.total_lines for item in files)
    unknown = sum(item.unrecognized_lines for item in files)

    yield "掃描 ", "secondary"
    yield str(len(files)), "value"
    yield " 個檔案、", "secondary"
    yield str(total), "value"
    yield " 個實體行；未辨識／疑似不同格式 ", "secondary"
    yield str(unknown), "warning" if unknown else "value"
    yield " 行。", "secondary"

    for item in files[:20]:
        yield "\n", "secondary"
        yield str(item.filename), "identifier"
        yield "：事件 ", "secondary"
        yield str(item.parsed_entries), "value"
        yield "；堆疊 ", "secondary"
        yield str(item.stacktrace_lines), "value"
        yield "；訊息續行 ", "secondary"
        yield str(item.continuation_lines), "value"
        yield "；空行 ", "secondary"
        yield str(item.blank_lines), "value"
        yield "；未辨識 ", "secondary"
        yield str(item.unrecognized_lines), "warning" if item.unrecognized_lines else "value"
        for number, sample in item.unrecognized_samples:
            yield "\n  第 ", "secondary"
            yield str(number), "value"
            yield " 行：", "secondary"
            yield str(sample), "value"

    if len(files) > 20:
        yield "\n另 ", "secondary"
        yield str(len(files) - 20), "value"
        yield " 個檔案未在此列出；總數包含所有檔案。", "secondary"

    yield "\n", "secondary"
    yield "分類在篩選前計算；續行採啟發式辨識，不能作為完整解析成功率。", "secondary"


def format_scan_coverage(diagnostics: ScanDiagnostics | None) -> str | None:
    """顯示逐檔掃描分類；非事件起始行的歸類不當作解析成功率。"""
    text = "".join(part for part, _role in iter_scan_coverage_parts(diagnostics))
    return text or None
