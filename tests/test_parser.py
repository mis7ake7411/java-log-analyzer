import pytest
import os
import shutil
from datetime import datetime
from log_analyzer.parser import parse_logs
import log_analyzer.parser as parser_module

@pytest.fixture
def temp_log_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "test.log"
    log_file.write_text(
        "2026-06-06 10:00:00.001 [main] INFO  com.test - Start\n"
        "2026-06-06 10:10:00.555 [http-1] ERROR com.test - Fail\n"
        "java.lang.RuntimeException: Error\n"
        "    at com.test.App.main\n"
        "2026-06-06 10:20:00.000 [main] INFO  com.test - End\n"
    )
    return str(d)

def test_parse_logs_counts(temp_log_dir):
    counts, errors = parse_logs(temp_log_dir)
    assert counts['INFO'] == 2
    assert counts['ERROR'] == 1

def test_parse_logs_keyword(temp_log_dir):
    # Test keyword filtering
    counts, errors = parse_logs(temp_log_dir, keyword="RuntimeException")
    assert counts['INFO'] == 0 # INFO lines don't have this keyword
    assert counts['ERROR'] == 1
    assert len(errors) == 1


def test_parse_logs_does_not_compile_regex_on_each_call(temp_log_dir, monkeypatch):
    def fail_compile(*_args, **_kwargs):
        raise AssertionError("regex should be compiled once at module import")

    monkeypatch.setattr(parser_module.re, "compile", fail_compile)
    
    class FakeDateTime:
        @staticmethod
        def strptime(value, fmt):
            return datetime(2026, 6, 6, 10, 0, 0)

    monkeypatch.setattr(parser_module, "datetime", FakeDateTime)

    counts, errors = parse_logs(temp_log_dir)

    assert counts['INFO'] == 2
    assert counts['ERROR'] == 1
    assert len(errors) == 3
