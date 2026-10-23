from log_analyzer.presentation.cli.args import build_argument_parser


def test_cli_accepts_max_export_mb_option():
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["--max-export-mb", "25"])

    assert args.max_export_mb == 25


def test_cli_accepts_split_by_level_option():
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["--split-by-level"])

    assert args.split_by_level is True


def test_cli_accepts_all_level_shortcut():
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["--level", "ALL"])

    assert args.level == ["ALL"]


def test_cli_positional_source_accepts_file_and_folder_help():
    parser = build_argument_parser(lambda: "1.0.0")

    args = parser.parse_args(["application.LOG"])

    assert args.dir == "application.LOG"
    assert "Log 檔案或資料夾路徑" in parser.format_help()
