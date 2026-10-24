import logging
import sys
from ...application.analysis_service import (
    AnalysisOptions,
    NoMatchingLogsError,
    run_analysis_with_options,
)
from ...domain.logback_pattern import UnsupportedLogbackPatternError
from ...domain.log_sources import LogSourceSelection, select_log_source
from ...infrastructure.exporting import ExportConflictError
from .args import build_argument_parser
from .runtime import (
    parse_datetime_value,
    resolve_levels,
    resolve_logback_pattern,
    resolve_output_path,
    resolve_target_dir,
)
from ..error_messages import get_error_hint
from ..encoding_warnings import format_encoding_warning
from log_analyzer.presentation.scan_diagnostics import format_scan_coverage
from ...version import get_package_version

logger = logging.getLogger(__name__)


def _print_error(title: str, message: str) -> None:
    print(f"錯誤：{message}")
    hint = get_error_hint(title, message)
    if hint:
        print(f"提示：{hint}")


def _handle_unexpected_error(exc: Exception) -> None:
    logger.exception("CLI 分析發生未預期錯誤", exc_info=exc)
    _print_error("執行失敗", str(exc))


def _try_run_tui(args) -> bool:
    """在要求時啟動 TUI，回傳是否已接手執行流程。"""
    if args.tui or (len(sys.argv) == 1 and sys.stdin.isatty()):
        try:
            from ..tui import LogAnalyzerApp

            app = LogAnalyzerApp()
            app.run()
            return True
        except ImportError:
            print("提示：欲使用 TUI 介面，請先安裝 textual 套件 (pip install textual)")
            if args.tui:
                sys.exit(1)
    return False


def _resolve_logback_pattern(
    args,
    target_dir: str,
    *,
    selected_files: tuple[str, ...] | None = None,
) -> str | None:
    """解析指定或自動推斷的 Logback pattern。"""
    try:
        selected_pattern, logback_notice = resolve_logback_pattern(
            args.logback_xml,
            target_dir,
            args.pattern,
            selected_files=selected_files,
        )
    except ValueError as exc:
        title = "輸入錯誤" if not str(exc).startswith("找不到符合條件的 log") else "無可分析資料"
        _print_error(title, str(exc))
        sys.exit(1)
    if logback_notice:
        print(logback_notice)
    return selected_pattern


def _print_analysis_result(result, args) -> None:
    """依既有 CLI 格式輸出分析結果與摘要。"""
    print(f"分析完成！報表已儲存至：{result.output_path} (格式: {args.format.upper()})")
    if len(result.exported_files) > 1:
        print(f"已自動分割為 {len(result.exported_files)} 個檔案：")
        for file_path in result.exported_files:
            print(f"  - {file_path}")
    if args.max_export_mb is not None:
        print(f"分割門檻：{args.max_export_mb} MB")
    if args.split_by_level:
        print("Level 分檔：啟用")

    print("\n符合條件的統計摘要 (Summary)：")
    for level, count in sorted(result.counts.items()):
        if count > 0:
            print(f"  {level}: {count}")
    encoding_warning = format_encoding_warning(getattr(result, "diagnostics", None))
    if encoding_warning:
        print(encoding_warning)
    coverage = format_scan_coverage(getattr(result, "diagnostics", None))
    if coverage:
        print(coverage)


def _print_source_scope(source: LogSourceSelection) -> None:
    """在分析前列出本次實際選取的日誌來源範圍。"""
    if source.is_file:
        print("來源模式：單一檔案")
    else:
        print("來源模式：資料夾（不含子目錄）")
    print(f"來源路徑：{source.path}")
    print(f"來源檔數：{len(source.filenames)}")
    print("完整檔名清單：")
    if source.filenames:
        for filename in source.filenames:
            print(f"  - {filename}")
    else:
        print("  （無）")


def _run_cli_analysis(args, target_dir: str) -> None:
    """建立分析設定、執行 CLI 分析並輸出結果。"""
    source = select_log_source(target_dir)
    _print_source_scope(source)
    if not source.filenames:
        raise ValueError("找不到支援的 Log 檔案")

    start_dt = parse_datetime_value(args.start, "開始時間")
    end_dt = parse_datetime_value(args.end, "結束時間")
    selected_levels = resolve_levels(args.level)
    print(f"分析 Level：{', '.join(selected_levels)}")
    if args.keyword:
        message = f"正在搜尋關鍵字：'{args.keyword}'"
        if args.ignore_case:
            message += " (忽略大小寫)"
        print(message)

    selected_pattern = _resolve_logback_pattern(
        args,
        source.path,
        selected_files=source.filenames,
    )
    options = AnalysisOptions(
        path=source.path,
        output_path=args.output,
        start_dt=start_dt,
        end_dt=end_dt,
        keyword=args.keyword,
        ignore_case=args.ignore_case,
        sort_by=args.sort,
        fmt=args.format,
        log_pattern=selected_pattern,
        max_export_bytes=None if args.max_export_mb is None else args.max_export_mb * 1024 * 1024,
        include_details=False,
        levels=selected_levels,
        split_by_level=args.split_by_level,
        selected_files=source.filenames,
        overwrite=args.overwrite,
    )
    _print_analysis_result(run_analysis_with_options(options), args)


def _handle_cli_error(exc: Exception) -> None:
    """輸出已知 CLI 錯誤並結束程式。"""
    if isinstance(exc, NoMatchingLogsError):
        encoding_warning = format_encoding_warning(exc.diagnostics)
        if encoding_warning:
            print(encoding_warning)
        coverage = format_scan_coverage(exc.diagnostics)
        if coverage:
            print(coverage)
    if isinstance(exc, ExportConflictError):
        paths = "\n".join(f"  - {path}" for path in exc.paths)
        _print_error(
            "匯出衝突",
            f"以下輸出檔案已存在：\n{paths}\n請更改 -o 輸出路徑，或明確指定 --overwrite 覆蓋。",
        )
    elif isinstance(exc, UnsupportedLogbackPatternError):
        _print_error("輸入錯誤", str(exc))
    elif isinstance(exc, PermissionError):
        _print_error("權限不足", str(exc))
    elif isinstance(exc, FileNotFoundError):
        _print_error("找不到來源", str(exc))
    elif isinstance(exc, ValueError):
        _print_error("輸入錯誤", str(exc))
    else:
        _handle_unexpected_error(exc)
    sys.exit(1)


def main():
    """程式的主要進入點，負責處理命令列參數與執行流程。"""
    parser = build_argument_parser(get_package_version)
    args = parser.parse_args()

    if _try_run_tui(args):
        return

    args.output = resolve_output_path(args.output, args.format)
    target_dir, notice = resolve_target_dir(args.dir)
    if notice:
        print(notice)

    try:
        _run_cli_analysis(args, target_dir)
    except Exception as exc:  # noqa: BLE001
        _handle_cli_error(exc)

if __name__ == '__main__':
    main()
