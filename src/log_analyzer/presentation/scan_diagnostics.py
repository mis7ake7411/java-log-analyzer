from __future__ import annotations

from log_analyzer.domain.log_types import ScanDiagnostics


def format_scan_coverage(diagnostics: ScanDiagnostics | None) -> str | None:
    """顯示逐檔掃描分類；非事件起始行的歸類不當作解析成功率。"""
    files = getattr(diagnostics, "file_coverage", ())
    if not files:
        return None
    total = sum(item.total_lines for item in files)
    unknown = sum(item.unrecognized_lines for item in files)
    lines = [f"掃描 {len(files)} 個檔案、{total} 個實體行；未辨識／疑似不同格式 {unknown} 行。"]
    for item in files[:20]:
        lines.append(
            f"{item.filename}：事件 {item.parsed_entries}；堆疊 {item.stacktrace_lines}；"
            f"訊息續行 {item.continuation_lines}；空行 {item.blank_lines}；未辨識 {item.unrecognized_lines}"
        )
        lines.extend(f"  第 {number} 行：{text}" for number, text in item.unrecognized_samples)
    if len(files) > 20:
        lines.append(f"另 {len(files) - 20} 個檔案未在此列出；總數包含所有檔案。")
    lines.append("分類在篩選前計算；續行採啟發式辨識，不能作為完整解析成功率。")
    return "\n".join(lines)
