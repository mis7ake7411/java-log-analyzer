from __future__ import annotations

import argparse


def build_argument_parser(get_package_version):
    """建立 CLI 參數解析器。"""
    parser = argparse.ArgumentParser(prog="log-analyzer", description="Java Logback 日誌分析工具")

    parser.add_argument("dir", nargs="?", default=".", help="包含 .log 檔案的目錄路徑 (預設: 當前目錄)")
    parser.add_argument("-f", "--format", choices=["csv", "json", "md"], default="csv", help="輸出的檔案格式 (預設: csv)")
    parser.add_argument("-o", "--output", help="輸出的報表路徑 (若未指定，將自動根據格式產生)")
    parser.add_argument("-k", "--keyword", help="欲搜尋的關鍵字 (例如：訂單編號或特定錯誤訊息)")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="搜尋時忽略大小寫")
    parser.add_argument("--start", help="過濾開始時間 (格式: YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--end", help="過濾結束時間 (格式: YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--pattern", help="自訂 Logback pattern，可貼上 logback.xml 的 <pattern> 內容")
    parser.add_argument("--logback-xml", help="從 logback.xml / logback-spring.xml 匯入並自動選擇最符合 log 樣本的 pattern")
    parser.add_argument("--sort", choices=["time", "level"], default="time", help="結果排序方式：time 時間排序，level 依 Log Level 分組排序")
    parser.add_argument("--max-export-mb", type=int, help="單一輸出檔案的最大大小 (MB)，超過時自動分割")
    parser.add_argument("--tui", action="store_true", help="啟動互動式介面 (TUI)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_package_version()}")
    return parser
