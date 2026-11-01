"""連接介面與核心處理的應用服務：分析保留結果，匯出使用已保留的結果。

TUI 分別呼叫 analyze_logs 與 export_analysis_result；CLI 使用
run_analysis_with_options 串起兩者。閱讀時先看 AnalysisRequest / AnalysisResult，
再看 analyze_logs，最後沿著 parse_log_directory 進入 domain 層。
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .summary import DashboardAnalysis, aggregate_dashboard_summary
from log_analyzer.domain.analysis_control import AnalysisControl
from log_analyzer.domain.log_types import MatchedLogs, ScanDiagnostics, SourceSnapshot
from log_analyzer.domain.log_sources import select_log_source
from log_analyzer.domain.parser import ParseOptions, parse_log_directory
from log_analyzer.infrastructure.exporting import ExportOptions, export_with_options
from log_analyzer.infrastructure.exporting.exporter import _stage_export_with_options
from log_analyzer.infrastructure.exporting.transaction import ExportTransaction
from log_analyzer.infrastructure.paths import ensure_writable_directory

DEFAULT_SELECTED_LEVELS = ("ERROR", "WARN", "INFO")


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """一次分析的輸入條件；不包含匯出路徑或格式。

    dataclass 自動產生初始化方法；frozen=True 禁止直接改寫欄位，
    slots=True 限定實例欄位。TUI 因而能保留按下分析當時的設定。
    型別中的 ``| None`` 表示可不提供；selected_files=None 表示依來源列出檔案。
    """

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
    """已保存結果的匯出設定；更改它不需要重新掃描日誌。

    fields=None 使用預設欄位，max_export_bytes=None 使用匯出器的預設門檻；
    overwrite 只授權本次覆蓋，不會改變分析條件。
    """

    output_path: str
    fmt: str = "csv"
    fields: tuple[str, ...] | None = None
    max_export_bytes: int | None = None
    split_by_level: bool = False
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    """CLI 等一次完成分析及匯出的設定，合併兩階段所需欄位。

    include_details=False 會在匯出完成後釋放明細，只保留摘要供呼叫者顯示。
    TUI 需要稍後瀏覽或重複匯出，因此使用分開的 Request / ExportOptions。
    """

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
    """一次分析的摘要、明細、來源快照與當次固定條件。

    matched_groups 是合併後的事件群組數；matched_occurrences 是出現總次數。
    matched_logs 可能是磁碟暫存儲存器，不一定是 list，使用完須釋放。
    field(default_factory=...) 會替每個實例建立自己的 dict / 診斷物件，避免共用。
    此類別未 frozen，成功匯出後可更新 output_path 與 exported_files。
    """

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
        """保留掃描診斷，讓介面能解釋零結果，而不是只顯示找不到資料。

        Args:
            diagnostics: 掃描診斷物件；傳入後可能被原地更新。

        Returns:
            None: 初始化帶掃描診斷的零結果例外。
        """
        self.diagnostics = diagnostics
        super().__init__("找不到符合條件的 log請確認目錄、關鍵字或資料內容")


def build_output_path(output_dir: str, output_name: str, fmt: str) -> str:
    """驗證輸出資料夾可寫入，清理檔名並依 fmt 補上副檔名。

    空白檔名改用時間戳；basename 去掉使用者輸入的目錄部分，
    讓結果留在 output_dir。資料夾無法使用時會拋出對應的檔案系統錯誤。

    Args:
        output_dir: 報表目的資料夾，必須可寫入。
        output_name: 使用者輸入的檔名；空白時產生時間戳檔名。
        fmt: 輸出格式：csv、json 或 md。

    Returns:
        str: 清理後的完整報表路徑。

    Raises:
        OSError: 輸出資料夾無法建立或不可寫入。
    """
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
    """公開相容性包裝器，保留既有呼叫介面。

    Args:
        path: 來源檔案或資料夾路徑。
        output_path: 預計發布的主要報表路徑。
        start_dt: 分析起點，含邊界；None 表示不限制。
        end_dt: 分析終點，含邊界；None 表示不限制。
        keyword: 關鍵字；None 或空字串表示不限制。
        ignore_case: 是否忽略關鍵字比對的大小寫。
        sort_by: 排序方式：time 或 level。
        fmt: 輸出格式：csv、json 或 md。
        log_pattern: 支援的 Logback Pattern；None 使用內建格式。
        max_export_bytes: 每個分檔的位元組門檻；None 使用匯出器預設。
        include_details: 匯出後是否保留可供再次讀取的明細。
        levels: 選定層級；None 表示不限，空集合表示不選任何層級。
        split_by_level: 是否在有多個有效層級時分開輸出。
        overwrite: 是否明確允許覆蓋這次輸出的同名一般檔案。

    Returns:
        AnalysisResult: 分析及匯出後的結果；明細是否保留由 include_details 決定。

    Raises:
        OSError: 目的路徑不安全、寫入或發布／還原失敗。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 指定欄位等匯出設定無效。
        ExportConflictError: 目標已存在且未授權覆蓋。
        NoMatchingLogsError: 沒有符合條件的事件。
    """
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
    """CLI 的完整流程：分析 → 匯出 → 依 include_details 決定是否保留明細。

    匯出失敗會釋放本次建立的明細暫存，再用 raise 將原例外傳回介面。
    成功且保留明細時，呼叫者接手其生命週期，應在不用時釋放。

    Args:
        options: AnalysisOptions，合併分析與匯出設定。

    Returns:
        AnalysisResult: 完成匯出後的結果與摘要。

    Raises:
        OSError: 目的路徑不安全、寫入或發布／還原失敗。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 指定欄位等匯出設定無效。
        ExportConflictError: 目標已存在且未授權覆蓋。
        NoMatchingLogsError: 沒有符合條件的事件。
    """
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
    """固定來源與條件、解析分組，再組成摘要與可重複讀取的分析結果。

    request 是輸入；control 可回報進度與要求取消。回傳 AnalysisResult，
    此時不寫報表。沒有符合資料時拋出 NoMatchingLogsError 並附診斷資訊；
    若建立摘要途中失敗或取消，會釋放明細暫存，成功時由呼叫者接手。

    Args:
        request: 本輪固定的 AnalysisRequest，包含來源與篩選條件。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        AnalysisResult: 尚未匯出、可重複瀏覽及匯出的結果。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 來源範圍無效或 Pattern 不受支援時停止處理。
        NoMatchingLogsError: 所有條件套用後沒有資料；例外附掃描診斷。
    """
    if control is not None:
        control.raise_if_cancelled()
    source = select_log_source(request.path, selected_files=request.selected_files)
    # 將實際選到的檔名寫進新請求，避免畫面後續編輯改變這一輪分析的意義。
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
    """使用 result 中保留的統計與明細產生報表，回傳實際輸出檔案清單。

    不重新分析來源；來源路徑會列為禁止覆寫的目標。只有成功產生檔案後
    才更新 result 的匯出資訊。明細已釋放時無法重建報表，必須重新分析。

    Args:
        result: 保留的 AnalysisResult；其明細尚未釋放。
        options: ResultExportOptions，只包含本次匯出設定。

    Returns:
        list[str]: 已發布報表的絕對路徑清單。

    Raises:
        ExportConflictError: 目標已存在且未授權覆蓋。
        OSError: 目的路徑不安全、寫入或發布／還原失敗。
        ValueError: 指定欄位等匯出設定無效。
    """
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
    """把報表與證據清單一起暫存及發布，回傳（報表路徑清單，清單路徑）。

    先完成所有檔案才呼叫 publish；finally 無論成功或失敗都要求清理暫存。
    這裡的交易是檔案發布保護，不是資料庫交易，也不保證斷電時整組原子性。

    Args:
        result: 保留的 AnalysisResult；其明細尚未釋放。
        options: ResultExportOptions，套用至報表與清單。

    Returns:
        tuple[list[str], str]: 報表絕對路徑清單與證據清單絕對路徑。

    Raises:
        ExportConflictError: 目標已存在且未授權覆蓋。
        OSError: 目的路徑不安全、寫入或發布／還原失敗。
        ValueError: 指定欄位等匯出設定無效。
    """
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
        from .evidence import _stage_evidence_manifest

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
    """從來源快照還原需保護的路徑，避免匯出時覆蓋原始日誌。

    Args:
        result: 保留的 AnalysisResult；其明細尚未釋放。

    Returns:
        tuple[Path, ...]: 本輪輸出不可覆蓋的來源路徑。
    """
    root = Path(result.input_path)
    sources: list[Path] = []
    if root.is_file():
        sources.append(root)
    for filename in result.source_states:
        candidate = Path(filename)
        sources.append(candidate if candidate.is_absolute() else root / candidate)
    return tuple(sources)


def _release_matched_logs(matched_logs: MatchedLogs) -> None:
    """有 release 方法才清理，讓 list 與磁碟儲存器可使用相同呼叫流程。

    Args:
        matched_logs: 可迭代的分析明細，可能為 list 或磁碟結果儲存器。

    Returns:
        None: 有 release 方法時執行清理。
    """
    release = getattr(matched_logs, "release", None)
    if callable(release):
        release()
