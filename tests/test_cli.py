import logging
from types import SimpleNamespace

import pytest

from log_analyzer.presentation import cli


def test_main_logs_unexpected_error_and_keeps_friendly_message(monkeypatch, caplog, capsys):
    def raise_unexpected(_options):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", "."])
    monkeypatch.setattr(cli, "resolve_target_dir", lambda _directory: (".", None))
    monkeypatch.setattr(cli, "resolve_logback_pattern", lambda *_args: (None, None))
    monkeypatch.setattr(cli, "run_analysis_with_options", raise_unexpected)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "錯誤：unexpected failure" in capsys.readouterr().out
    assert "unexpected failure" in caplog.text


def test_main_passes_analysis_options_and_prints_success_result(monkeypatch, capsys):
    captured_options = None

    def run_with_options(options):
        nonlocal captured_options
        captured_options = options
        return SimpleNamespace(
            output_path="/tmp/report.csv",
            exported_files=["/tmp/report.csv"],
            counts={"ERROR": 2, "INFO": 0},
        )

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", "logs", "--keyword", "Order", "--ignore-case"])
    monkeypatch.setattr(cli, "resolve_target_dir", lambda _directory: ("/logs", None))
    monkeypatch.setattr(cli, "resolve_logback_pattern", lambda *_args: ("%msg%n", None))
    monkeypatch.setattr(cli, "run_analysis_with_options", run_with_options)

    cli.main()

    assert captured_options is not None
    assert captured_options.path == "/logs"
    assert captured_options.output_path.endswith(".csv")
    assert captured_options.keyword == "Order"
    assert captured_options.ignore_case is True
    assert captured_options.log_pattern == "%msg%n"
    assert captured_options.include_details is False
    assert captured_options.levels == ("ERROR", "WARN", "INFO")
    assert capsys.readouterr().out.splitlines() == [
        "正在分析目錄：/logs",
        "分析 Level：ERROR, WARN, INFO",
        "正在搜尋關鍵字：'Order' (忽略大小寫)",
        "分析完成！報表已儲存至：/tmp/report.csv (格式: CSV)",
        "",
        "符合條件的統計摘要 (Summary)：",
        "  ERROR: 2",
    ]
