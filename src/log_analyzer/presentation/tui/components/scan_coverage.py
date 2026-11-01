"""以 Rich Text 呈現共用的掃描覆蓋診斷摘要。"""

from rich.text import Text

from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.presentation.scan_diagnostics import iter_scan_coverage_parts
from log_analyzer.presentation.tui.components.dashboard_text import (
    dashboard_identifier_style,
)
from log_analyzer.presentation.tui.dashboard_styles import (
    DASHBOARD_PALETTE,
    DashboardCardCategory,
)


def build_scan_coverage_text(diagnostics: ScanDiagnostics | None) -> Text | None:
    """將純文字 coverage parts 組成不解析原始樣本的 Rich Text。"""
    text = Text()
    has_text = False
    for part, role in iter_scan_coverage_parts(diagnostics):
        has_text = True
        if role == "identifier":
            style = dashboard_identifier_style(DashboardCardCategory.CONTEXT)
        elif role == "warning":
            style = DASHBOARD_PALETTE.warning_style
        elif role == "value":
            style = DASHBOARD_PALETTE.value_style
        else:
            style = DASHBOARD_PALETTE.secondary_style
        text.append(part, style=style)
    return text if has_text else None
