import os
import sys
from ..application.analysis_service import run_analysis
from ..domain.logback_pattern import UnsupportedLogbackPatternError
from .cli_args import build_argument_parser
from .cli_runtime import (
    parse_datetime_value,
    resolve_logback_pattern,
    resolve_output_path,
    resolve_target_dir,
)
from .error_messages import get_error_hint
from ..version import get_package_version


def _print_error(title: str, message: str) -> None:
    print(f"錯誤：{message}")
    hint = get_error_hint(title, message)
    if hint:
        print(f"提示：{hint}")


def main():
    """
    程式的主要進入點，負責處理命令列參數與執行流程。
    """
    parser = build_argument_parser(get_package_version)
    args = parser.parse_args()

    if args.tui or (len(sys.argv) == 1 and sys.stdin.isatty()):
        try:
            from .tui import LogAnalyzerApp
            app = LogAnalyzerApp()
            app.run()
            return
        except ImportError:
            print("提示：欲使用 TUI 介面，請先安裝 textual 套件 (pip install textual)")
            if args.tui: sys.exit(1)

    args.output = resolve_output_path(args.output, args.format)
    target_dir, notice = resolve_target_dir(args.dir)
    if notice:
        print(notice)

    try:
        start_dt = parse_datetime_value(args.start, "開始時間")
        end_dt = parse_datetime_value(args.end, "結束時間")
        print(f"正在分析目錄：{os.path.abspath(target_dir)}")
        if args.keyword:
            msg = f"正在搜尋關鍵字：'{args.keyword}'"
            if args.ignore_case:
                msg += " (忽略大小寫)"
            print(msg)

        selected_pattern = args.pattern
        try:
            selected_pattern, logback_notice = resolve_logback_pattern(
                args.logback_xml,
                target_dir,
                selected_pattern,
            )
        except ValueError as exc:
            _print_error("輸入錯誤" if not str(exc).startswith("找不到符合條件的 log") else "無可分析資料", str(exc))
            sys.exit(1)
        if logback_notice:
            print(logback_notice)

        result = run_analysis(
            target_dir,
            args.output,
            start_dt,
            end_dt,
            args.keyword,
            args.ignore_case,
            args.sort,
            args.format,
            selected_pattern,
            None if args.max_export_mb is None else args.max_export_mb * 1024 * 1024,
            False,
        )

        print(f"分析完成！報表已儲存至：{result.output_path} (格式: {args.format.upper()})")
        if len(result.exported_files) > 1:
            print(f"已自動分割為 {len(result.exported_files)} 個檔案：")
            for file_path in result.exported_files:
                print(f"  - {file_path}")
        if args.max_export_mb is not None:
            print(f"分割門檻：{args.max_export_mb} MB")
        
        print("\n符合條件的統計摘要 (Summary)：")
        for level, count in sorted(result.counts.items()):
            if count > 0:
                print(f"  {level}: {count}")
            
    except UnsupportedLogbackPatternError as e:
        _print_error("輸入錯誤", str(e))
        sys.exit(1)
    except PermissionError as e:
        _print_error("權限不足", str(e))
        sys.exit(1)
    except FileNotFoundError as e:
        _print_error("找不到資料夾", str(e))
        sys.exit(1)
    except ValueError as e:
        _print_error("輸入錯誤", str(e))
        sys.exit(1)
    except Exception as e:
        _print_error("執行失敗", str(e))
        sys.exit(1)

if __name__ == '__main__':
    main()
