from log_analyzer.presentation.cli.args import build_argument_parser


def test_cli_accepts_max_export_mb_option():
    """情境：CLI 應接受 max-export-mb 參數。"""
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["--max-export-mb", "25"])

    assert args.max_export_mb == 25


def test_cli_accepts_split_by_level_option():
    """情境：CLI 應接受 split-by-level 參數。"""
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["--split-by-level"])

    assert args.split_by_level is True


def test_cli_overwrite_defaults_to_false_and_can_be_enabled():
    """情境：overwrite 應預設關閉且可明確啟用。"""
    parser = build_argument_parser(lambda: "1.0.0")

    assert parser.parse_args([]).overwrite is False
    assert parser.parse_args(["--overwrite"]).overwrite is True


def test_cli_accepts_all_level_shortcut():
    """情境：CLI 應接受 ALL 層級簡寫。"""
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["--level", "ALL"])

    assert args.level == ["ALL"]


def test_cli_positional_source_accepts_file_and_folder_help():
    """情境：位置參數說明應清楚包含單檔與資料夾。"""
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["application.LOG"])

    assert args.dir == "application.LOG"
    assert "Log 檔案或資料夾路徑" in parser.format_help()
