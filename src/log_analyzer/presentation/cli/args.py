from __future__ import annotations

import argparse


def build_argument_parser(get_package_version):
    """建立同時支援批次分析與持續追蹤的 CLI 參數解析器。

    Args:
        get_package_version: 回傳目前套件版本字串的函式。

    Returns:
        argparse.ArgumentParser: 已設定來源、分析條件及輸出模式的解析器。
    """
    parser = argparse.ArgumentParser(prog="log-analyzer", description="Java Logback 日誌分析工具")

    parser.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Log 檔案或資料夾路徑 (預設: 當前目錄)",
    )
    parser.add_argument("-f", "--format", choices=["csv", "json", "md"], default="csv", help="輸出的檔案格式 (預設: csv)")
    parser.add_argument("-o", "--output", help="輸出的報表路徑 (若未指定，將自動根據格式產生)")
    parser.add_argument("-k", "--keyword", help="欲搜尋的關鍵字 (例如：訂單編號或特定錯誤訊息)")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="搜尋時忽略大小寫")
    parser.add_argument(
        "--level",
        action="append",
        type=str.upper,
        choices=["ALL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG", "TRACE"],
        help="要納入分析的 Log Level，可重複指定；ALL 代表所有常見 Level (預設: ERROR/WARN/INFO)",
    )
    parser.add_argument("--start", help="過濾開始時間 (格式: YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--end", help="過濾結束時間 (格式: YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--pattern", help="自訂 Logback pattern，可貼上 logback.xml 的 <pattern> 內容")
    parser.add_argument("--logback-xml", help="從 logback.xml / logback-spring.xml 匯入並自動選擇最符合 log 樣本的 pattern")
    parser.add_argument("--sort", choices=["time", "level"], default="time", help="結果排序方式：time 時間排序，level 依 Log Level 分組排序")
    parser.add_argument("--max-export-mb", type=int, help="單一輸出檔案的最大大小 (MB)，超過時自動分割")
    parser.add_argument("--split-by-level", action="store_true", help="依 Log Level 分開輸出檔案；只有多個 Level 有資料時才分檔")
    parser.add_argument("--overwrite", action="store_true", help="明確允許覆蓋已存在的輸出檔案")
    parser.add_argument("--tui", action="store_true", help="啟動互動式介面 (TUI)")
    parser.add_argument("--follow", action="store_true", help="持續追蹤單一日誌檔案的新事件")
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_package_version()}")
    return parser


def validate_follow_arguments(
    argv: list[str],
    parser: argparse.ArgumentParser | None = None,
) -> None:
    """拒絕與持續追蹤互斥的批次匯出選項。

    Args:
        argv: 不含程式名稱的原始命令列參數。
        parser: 已建立的 CLI 解析器；用來沿用其選項縮寫契約。

    Returns:
        None: 參數相容時正常返回。

    Raises:
        ValueError: 同時明確指定批次匯出選項或 TUI 時。
    """
    active_parser = parser or build_argument_parser(lambda: "")
    option_actions = active_parser._option_string_actions
    incompatible_long_options = {
        "--format",
        "--output",
        "--sort",
        "--max-export-mb",
        "--split-by-level",
        "--overwrite",
        "--tui",
    }
    incompatible_short_options = {"-f", "-o"}
    conflicts: list[str] = []

    for argument in argv:
        if argument == "--":
            break

        if argument.startswith("--"):
            supplied_option = argument.partition("=")[0]
            matching_options = [
                option
                for option in option_actions
                if option.startswith("--") and option.startswith(supplied_option)
            ]
            if len(matching_options) == 1 and matching_options[0] in incompatible_long_options:
                conflicts.append(matching_options[0])
            continue

        if not argument.startswith("-") or argument == "-":
            continue

        cluster = argument[1:]
        for character in cluster:
            option = f"-{character}"
            action = option_actions.get(option)
            if action is None:
                break
            if option in incompatible_short_options:
                conflicts.append(option)
                break
            if action.nargs != 0:
                break

    if conflicts:
        options = ", ".join(dict.fromkeys(conflicts))
        raise ValueError(f"--follow 不可與批次匯出參數或 --tui 一起使用：{options}")
