import logging

import pytest

from log_analyzer.presentation import cli


def test_main_logs_unexpected_error_and_keeps_friendly_message(monkeypatch, caplog, capsys):
    def raise_unexpected(**_kwargs):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", "."])
    monkeypatch.setattr(cli, "resolve_target_dir", lambda _directory: (".", None))
    monkeypatch.setattr(cli, "resolve_logback_pattern", lambda *_args: (None, None))
    monkeypatch.setattr(cli, "run_analysis", raise_unexpected)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "錯誤：unexpected failure" in capsys.readouterr().out
    assert "unexpected failure" in caplog.text
