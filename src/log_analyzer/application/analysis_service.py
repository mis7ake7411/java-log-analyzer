from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dashboard_summary import DashboardAnalysis, aggregate_dashboard_summary
from ..domain.analysis_control import AnalysisControl
from ..domain.log_types import MatchedLogs, ScanDiagnostics, SourceSnapshot
from ..domain.log_sources import select_log_source
from ..domain.parser import ParseOptions, parse_log_directory
from ..infrastructure.exporting import ExportOptions, export_with_options
from ..infrastructure.exporting.exporter import _stage_export_with_options
from ..infrastructure.exporting.transaction import ExportTransaction
from ..infrastructure.paths import ensure_writable_directory

DEFAULT_SELECTED_LEVELS = ("ERROR", "WARN", "INFO")


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """描述一次來源日誌分析所需的條件。"""

    path: str
    start_dt: datetime | None = None
    end_dt: datetime | None = None
    keyword: str | None = None
    ignore_case: bool = False
    sort_by: str = "time"
    log_pattern: str | None = None
    levels: tuple[str, ...] | None = DEFAULT_SELECTED_LEVELS
    selected_files: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ResultExportOptions:
    """描述如何匯出已分析完成的結果。"""

    output_path: str
    fmt: str = "csv"
    fields: tuple[str, ...] | None = None
    max_export_bytes: int | None = None
    split_by_level: bool = False
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    """分析執行所需的設定。"""

    path: str
    output_path: str
    start_dt: datetime | None
    end_dt: datetime | None
    keyword: str | None
    ignore_case: bool
    sort_by: str
    fmt: str
    log_pattern: str | None = None
    max_export_bytes: int | None = None
    include_details: bool = True
    levels: tuple[str, ...] | None = DEFAULT_SELECTED_LEVELS
    split_by_level: bool = False
    selected_files: tuple[str, ...] | None = None
    overwrite: bool = False


@dataclass(slots=True)
class AnalysisResult:
    """一次分析執行的完整結果"""

    input_path: str
    output_path: str
    format_name: str
    keyword: str
    ignore_case: bool
    total_logs: int
    matched_groups: int
    matched_occurrences: int
    level_summary: list[tuple[str, int]]
    selected_levels: tuple[str, ...]
    sort_by: str
    counts: Counter[str]
    matched_logs: MatchedLogs
    exported_files: list[str]
    dashboard_summary: DashboardAnalysis | None = None
    source_states: dict[str, SourceSnapshot] = field(default_factory=dict)
    diagnostics: ScanDiagnostics = field(default_factory=ScanDiagnostics)
    analysis_request: AnalysisRequest | None = None


class NoMatchingLogsError(ValueError):
    """沒有符合分析條件的 Log，並保留掃描階段的診斷資訊。"""

    def __init__(self, diagnostics: ScanDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__("找不到符合條件的 log請確認目錄、關鍵字或資料內容")


def build_output_path(output_dir: str, output_name: str, fmt: str) -> str:
    """把輸出資料夾與檔名組合成完整輸出路徑"""
    normalized_dir = ensure_writable_directory(output_dir)
    cleaned = output_name.strip()
    if not cleaned:
        cleaned = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cleaned = os.path.basename(cleaned)
    base_name, _ = os.path.splitext(cleaned)
    if not base_name:
        base_name = cleaned
    return os.path.join(normalized_dir, f"{base_name}.{fmt}")


def run_analysis(  # noqa: PLR0913 - public legacy API retained for backward compatibility
    path: str,
    output_path: str,
    start_dt: datetime | None,
    end_dt: datetime | None,
    keyword: str | None,
    ignore_case: bool,
    sort_by: str,
    fmt: str,
    log_pattern: str | None = None,
    max_export_bytes: int | None = None,
    include_details: bool = True,
    levels: tuple[str, ...] | None = DEFAULT_SELECTED_LEVELS,
    split_by_level: bool = False,
    *,
    overwrite: bool = False,
) -> AnalysisResult:
    """公開相容性包裝器，保留既有呼叫介面。"""
    return run_analysis_with_options(
        AnalysisOptions(
            path=path,
            output_path=output_path,
            start_dt=start_dt,
            end_dt=end_dt,
            keyword=keyword,
            ignore_case=ignore_case,
            sort_by=sort_by,
            fmt=fmt,
            log_pattern=log_pattern,
            max_export_bytes=max_export_bytes,
            include_details=include_details,
            levels=levels,
            split_by_level=split_by_level,
            overwrite=overwrite,
        )
    )


def run_analysis_with_options(options: AnalysisOptions) -> AnalysisResult:
    """依分析設定執行分析、匯出，並回傳摘要結果。"""
    result = analyze_logs(
        AnalysisRequest(
            path=options.path,
            start_dt=options.start_dt,
            end_dt=options.end_dt,
            keyword=options.keyword,
            ignore_case=options.ignore_case,
            sort_by=options.sort_by,
            log_pattern=options.log_pattern,
            levels=options.levels,
            selected_files=options.selected_files,
        )
    )
    try:
        export_analysis_result(
            result,
            ResultExportOptions(
                output_path=options.output_path,
                fmt=options.fmt,
                max_export_bytes=options.max_export_bytes,
                split_by_level=options.split_by_level,
                overwrite=options.overwrite,
            ),
        )
    except Exception:
        _release_matched_logs(result.matched_logs)
        raise

    if not options.include_details:
        _release_matched_logs(result.matched_logs)
        result.matched_logs = []
    return result


def analyze_logs(
    request: AnalysisRequest,
    *,
    control: AnalysisControl | None = None,
) -> AnalysisResult:
    """掃描來源日誌並保留可供後續匯出的分析結果。"""
    if control is not None:
        control.raise_if_cancelled()
    source = select_log_source(request.path, selected_files=request.selected_files)
    normalized_levels = None if request.levels is None else tuple(request.levels)
    normalized_request = AnalysisRequest(
        path=source.path,
        start_dt=request.start_dt,
        end_dt=request.end_dt,
        keyword=request.keyword,
        ignore_case=request.ignore_case,
        sort_by=request.sort_by,
        log_pattern=request.log_pattern,
        levels=normalized_levels,
        selected_files=source.filenames,
    )
    parse_options = ParseOptions(
        start_time=normalized_request.start_dt,
        end_time=normalized_request.end_dt,
        keyword=normalized_request.keyword,
        ignore_case=normalized_request.ignore_case,
        log_pattern=normalized_request.log_pattern,
        sort_by=normalized_request.sort_by,
        levels=normalized_request.levels,
        selected_files=source.filenames,
    )
    if control is None:
        counts, matched_logs = parse_log_directory(source.root, parse_options)
    else:
        counts, matched_logs = parse_log_directory(source.root, parse_options, control=control)
    diagnostics = getattr(matched_logs, "diagnostics", ScanDiagnostics())

    try:
        if control is not None:
            control.raise_if_cancelled()
        if not counts and not matched_logs:
            raise NoMatchingLogsError(diagnostics)

        source_states: dict[str, SourceSnapshot] = dict(getattr(matched_logs, "source_states", {}))
        if control is None:
            dashboard_summary, matched_occurrences = aggregate_dashboard_summary(matched_logs)
        else:
            dashboard_summary, matched_occurrences = aggregate_dashboard_summary(
                matched_logs,
                control=control,
            )
        result = AnalysisResult(
            input_path=source.root,
            output_path="",
            format_name="未匯出",
            keyword=normalized_request.keyword or "未設定",
            ignore_case=normalized_request.ignore_case,
            total_logs=sum(counts.values()),
            matched_groups=len(matched_logs),
            matched_occurrences=matched_occurrences,
            level_summary=[(level, count) for level, count in sorted(counts.items()) if count > 0],
            selected_levels=tuple(normalized_levels or ()),
            sort_by=normalized_request.sort_by,
            counts=counts,
            matched_logs=matched_logs,
            exported_files=[],
            dashboard_summary=dashboard_summary,
            source_states=source_states,
            diagnostics=diagnostics,
            analysis_request=normalized_request,
        )
        if control is not None:
            control.raise_if_cancelled()
        return result
    except Exception:
        _release_matched_logs(matched_logs)
        raise


def export_analysis_result(
    result: AnalysisResult,
    options: ResultExportOptions,
) -> list[str]:
    """將保留的分析結果匯出，不重新讀取來源日誌。"""
    if result.matched_groups > 0 and len(result.matched_logs) == 0:
        raise ValueError("匹配日誌明細已釋放，無法再次匯出；請重新分析後再試。")

    exported_files = [
        os.path.abspath(path)
        for path in export_with_options(
            ExportOptions(
                counts=result.counts,
                matched_logs=result.matched_logs,
                output_path=options.output_path,
                format=options.fmt,
                max_export_bytes=options.max_export_bytes,
                split_by_level=options.split_by_level,
                fields=options.fields,
                overwrite=options.overwrite,
            ),
            protected_paths=_source_paths(result),
        )
    ]
    if exported_files:
        result.output_path = exported_files[0]
        result.format_name = options.fmt
        result.exported_files = exported_files
    return exported_files


def export_analysis_bundle(
    result: AnalysisResult,
    options: ResultExportOptions,
) -> tuple[list[str], str]:
    """將報表與證據清單放在同一個匯出交易中準備並發布。"""
    if result.matched_groups > 0 and len(result.matched_logs) == 0:
        raise ValueError("匹配日誌明細已釋放，無法再次匯出；請重新分析後再試。")

    transaction = ExportTransaction(
        options.output_path,
        overwrite=options.overwrite,
        protected_paths=_source_paths(result),
    )
    try:
        report_staged = _stage_export_with_options(
            ExportOptions(
                counts=result.counts,
                matched_logs=result.matched_logs,
                output_path=options.output_path,
                format=options.fmt,
                max_export_bytes=options.max_export_bytes,
                split_by_level=options.split_by_level,
                fields=options.fields,
                overwrite=options.overwrite,
            ),
            transaction,
        )
        from .evidence_manifest import _stage_evidence_manifest

        manifest_staged = _stage_evidence_manifest(
            result,
            options,
            report_staged,
            transaction,
        )
        published = transaction.publish([*report_staged, manifest_staged])
        exported_files = [os.path.abspath(path) for path in published[: len(report_staged)]]
        manifest_path = os.path.abspath(published[-1])
        if exported_files:
            result.output_path = exported_files[0]
            result.format_name = options.fmt
            result.exported_files = exported_files
        return exported_files, manifest_path
    finally:
        transaction.cleanup()


def _source_paths(result: AnalysisResult) -> tuple[Path, ...]:
    root = Path(result.input_path)
    sources: list[Path] = []
    if root.is_file():
        sources.append(root)
    for filename in result.source_states:
        candidate = Path(filename)
        sources.append(candidate if candidate.is_absolute() else root / candidate)
    return tuple(sources)


def _release_matched_logs(matched_logs: MatchedLogs) -> None:
    release = getattr(matched_logs, "release", None)
    if callable(release):
        release()
