"""程式入口：解析命令列參數，再選擇 TUI 或一次性的 CLI 分析流程。

閱讀順序：main → _try_run_tui / _run_cli_analysis → application.analysis.service。
本層負責輸入與訊息呈現，實際掃描、分組與匯出交由下層處理。
"""

import logging
import sys
from ...application.analysis.service import (
    AnalysisOptions,
    NoMatchingLogsError,
    run_analysis_with_options,
)
from ...domain.logback.pattern import UnsupportedLogbackPatternError
from ...domain.log_sources import LogSourceSelection, select_log_source
from ...infrastructure.exporting import ExportConflictError
from .args import build_argument_parser, validate_follow_arguments
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
    """列出錯誤及對應的修正提示；只負責顯示，不決定程式何時退出。

    Args:
        title: 用於挑選修正提示的錯誤分類。
        message: 要顯示的可讀錯誤訊息。

    Returns:
        None: 印出錯誤與可用的提示。
    """
    print(f"錯誤：{message}")
    hint = get_error_hint(title, message)
    if hint:
        print(f"提示：{hint}")


def _handle_unexpected_error(exc: Exception) -> None:
    """將非預期錯誤的堆疊寫入 logger，並向使用者顯示可讀的訊息。

    Args:
        exc: 要記錄或呈現的例外物件。

    Returns:
        None: 記錄例外並印出可讀訊息。
    """
    logger.exception("CLI 分析發生未預期錯誤", exc_info=exc)
    _print_error("執行失敗", str(exc))


def _try_run_tui(args) -> bool:
    """指定 --tui，或無參數且標準輸入為終端機時，啟動互動介面。

    回傳 True 表示介面已接手，main 不應再執行 CLI 分析。
    將 Textual 的 import 放在函式內，避免純 CLI 使用者也要先載入畫面元件。

    Args:
        args: argparse 解析後的命令列參數物件。

    Returns:
        bool: TUI 已接手執行時為 True。

    Raises:
        SystemExit: 明確要求 TUI 但其套件無法匯入。
    """
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
    """解析指定或自動推斷的 Logback pattern。

    Args:
        args: argparse 解析後的命令列參數物件。
        target_dir: 已解析的日誌來源路徑。
        selected_files: 固定檔名範圍；None 讓來源選擇器決定，空 tuple 不選任何檔案。

    Returns:
        str | None: 明確解析的 Pattern，或 None 使用預設。

    Raises:
        SystemExit: XML 或 Pattern 無法確認時以非零狀態退出。
    """
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
    """依既有 CLI 格式輸出分析結果與摘要。

    Args:
        result: 保留的 AnalysisResult；其明細尚未釋放。
        args: argparse 解析後的命令列參數物件。

    Returns:
        None: 印出成功摘要、檔案清單與診斷。
    """
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
    """在分析前列出本次實際選取的日誌來源範圍。

    Args:
        source: 已固定來源模式、根目錄與檔名的 LogSourceSelection。

    Returns:
        None: 印出固定來源模式與完整檔名。
    """
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
    """把 args 轉成 AnalysisOptions，完成分析與匯出後印出摘要。

    args 是 argparse 產生的參數物件。這裡先固定實際檔案範圍，再解析
    日期、Level 與 Pattern；include_details=False 讓服務在匯出後釋放明細暫存。

    Args:
        args: argparse 解析後的命令列參數物件。
        target_dir: 已解析的日誌來源路徑。

    Returns:
        None: 執行分析及匯出後印出結果。

    Raises:
        OSError: 目的路徑不安全、寫入或發布／還原失敗。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 指定欄位等匯出設定無效。
        ExportConflictError: 目標已存在且未授權覆蓋。
        NoMatchingLogsError: 沒有符合條件的日誌。
        SystemExit: Pattern 輸入無法解析。
    """
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
    """輸出已知 CLI 錯誤並結束程式。

    Args:
        exc: 要記錄或呈現的例外物件。

    Returns:
        None: 顯示錯誤後結束，不正常返回。

    Raises:
        SystemExit: 以狀態碼 1 結束 CLI。
    """
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


def _run_cli_follow(args) -> None:
    """以非互動 CLI 啟動單一日誌檔案的持續追蹤。

    Args:
        args: argparse 解析後且已通過 follow 相容性檢查的參數。

    Returns:
        None: 追蹤正常結束後返回。

    Raises:
        KeyboardInterrupt: asyncio 中斷訊號傳回呼叫端，轉為退出碼 130。
        Exception: 輸入、讀取、解析或事件輸出失敗。
    """
    import asyncio

    from .follow import run_follow

    asyncio.run(run_follow(args))


def main():
    """建立參數解析器，分流 TUI、follow 與批次 CLI。

    先保留原始參數以檢查 follow 的批次匯出互斥選項。follow 在解析預設匯出路徑前執行，
    批次流程則沿用原本 TUI 判斷、路徑解析及分析錯誤處理。

    Returns:
        None: CLI 完成或 TUI 關閉後返回。

    Raises:
        SystemExit: 參數錯誤、help／version、Ctrl+C 或分析失敗時結束程序。
    """
    parser = build_argument_parser(get_package_version)
    argv = sys.argv[1:]
    args = parser.parse_args(argv)

    if args.follow:
        try:
            validate_follow_arguments(argv, parser)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            _run_cli_follow(args)
        except KeyboardInterrupt:
            raise SystemExit(130) from None
        except Exception as exc:  # noqa: BLE001
            print(f"錯誤：{exc}", file=sys.stderr)
            raise SystemExit(1) from None
        return

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
