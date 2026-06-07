import argparse
import sys
import os
from importlib.metadata import PackageNotFoundError, version as package_version
from datetime import datetime
from .naming import build_timestamped_name
from .parser import parse_logs
from .exporter import export_results
from .logback_pattern import UnsupportedLogbackPatternError


def get_package_version() -> str:
    try:
        return package_version("java-log-analyzer")
    except PackageNotFoundError:
        return "unknown"


def main():
    """
    程式的主要進入點，負責處理命令列參數與執行流程。
    """
    # 建立一個參數解析器
    parser = argparse.ArgumentParser(prog='log-analyzer', description='Java Logback 日誌分析工具')
    
    # 參數定義
    parser.add_argument('dir', nargs='?', default='.', help='包含 .log 檔案的目錄路徑 (預設: 當前目錄)')
    
    # --- 格式支援功能參數 ---
    parser.add_argument('-f', '--format', choices=['csv', 'json', 'md'], default='csv', help='輸出的檔案格式 (預設: csv)')
    
    parser.add_argument('-o', '--output', help='輸出的報表路徑 (若未指定，將自動根據格式產生)')
    
    # --- 關鍵字搜尋功能參數 ---
    parser.add_argument('-k', '--keyword', help='欲搜尋的關鍵字 (例如：訂單編號或特定錯誤訊息)')
    parser.add_argument('-i', '--ignore-case', action='store_true', help='搜尋時忽略大小寫')
    
    parser.add_argument('--start', help='過濾開始時間 (格式: YYYY-MM-DD HH:MM:SS)')
    parser.add_argument('--end', help='過濾結束時間 (格式: YYYY-MM-DD HH:MM:SS)')
    parser.add_argument('--pattern', help='自訂 Logback pattern，可貼上 logback.xml 的 <pattern> 內容')
    
    # --- TUI 參數 ---
    parser.add_argument('--tui', action='store_true', help='啟動互動式介面 (TUI)')
    parser.add_argument('--version', action='version', version=f'%(prog)s {get_package_version()}')
    
    args = parser.parse_args()
    
    # --- 決定是否啟動 TUI ---
    # 如果使用者下達 --tui，或者在沒給予任何必要參數的情況下啟動，則執行 TUI
    if args.tui or (len(sys.argv) == 1 and sys.stdin.isatty()):
        try:
            from .tui import LogAnalyzerApp
            app = LogAnalyzerApp()
            app.run()
            return
        except ImportError:
            print("提示：欲使用 TUI 介面，請先安裝 textual 套件 (pip install textual)")
            if args.tui: sys.exit(1)

    # 處理預設輸出檔名
    output_ext = args.format
    if not args.output:
        args.output = f'{build_timestamped_name("log_analysis")}.{output_ext}'

    # 智慧偵測資料夾
    target_dir = args.dir
    if target_dir == '.' and os.path.isdir('logs'):
        target_dir = 'logs'
        print(f"偵測到 'logs' 資料夾，將自動分析該目錄...")

    try:
        start_dt = datetime.strptime(args.start, '%Y-%m-%d %H:%M:%S') if args.start else None
        end_dt = datetime.strptime(args.end, '%Y-%m-%d %H:%M:%S') if args.end else None
    except ValueError as e:
        print(f"錯誤：時間格式不正確。請使用 YYYY-MM-DD HH:MM:SS。{e}")
        sys.exit(1)
    
    try:
        print(f"正在分析目錄：{os.path.abspath(target_dir)}")
        if args.keyword:
            msg = f"正在搜尋關鍵字：'{args.keyword}'"
            if args.ignore_case:
                msg += " (忽略大小寫)"
            print(msg)

        # 執行解析邏輯
        counts, matched_logs = parse_logs(
            target_dir,
            start_dt,
            end_dt,
            args.keyword,
            args.ignore_case,
            args.pattern,
        )
        
        if not counts and not matched_logs:
            print("結果：找不到符合條件的日誌內容。")
            return

        # 執行匯出邏輯 (使用新的通用匯出函式)
        export_results(counts, matched_logs, args.output, args.format)
        
        print(f"分析完成！報表已儲存至：{args.output} (格式: {args.format.upper()})")
        
        print("\n符合條件的統計摘要 (Summary)：")
        for level, count in sorted(counts.items()):
            if count > 0:
                print(f"  {level}: {count}")
            
    except UnsupportedLogbackPatternError as e:
        print(f"錯誤：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"執行時發生錯誤：{e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
