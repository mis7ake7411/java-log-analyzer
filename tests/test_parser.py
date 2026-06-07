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


def test_parse_logs_with_custom_logback_pattern(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "custom.log"
    log_file.write_text(
        "2026-06-06 10:10:00.555 ERROR [http-1] com.test.App App.java:42 - Fail\n"
        "java.lang.RuntimeException: Error\n"
        "    at com.test.App.main\n"
    )

    counts, errors = parse_logs(
        str(d),
        log_pattern="%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} %file:%line - %msg%n",
    )

    assert counts["ERROR"] == 1
    assert len(errors) == 1
    assert errors[0]["thread"] == "http-1"
    assert errors[0]["logger"] == "com.test.App"
    assert errors[0]["message"] == "Fail"
    assert "RuntimeException" in errors[0]["stacktrace"]


def test_parse_logs_with_spring_boot_file_pattern_defaults(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "spring.log"
    log_file.write_text(
        "2026-06-06 10:10:00.555 ERROR[http-1][pid- ][line-42] "
        "com.test.App                             : Fail\n"
    )

    counts, errors = parse_logs(
        str(d),
        log_pattern=(
            "${FILE_LOG_PATTERN:-%d{${LOG_DATEFORMAT_PATTERN:-yyyy-MM-dd HH:mm:ss.SSS}} "
            "${LOG_LEVEL_PATTERN:-%5p}[%t][pid-${PID:- }][line-%line] "
            "%-40.40logger{39} : %m%n${LOG_EXCEPTION_CONVERSION_WORD:-%wEx}}"
        ),
    )

    assert counts["ERROR"] == 1
    assert errors[0]["thread"] == "http-1"
    assert errors[0]["logger"] == "com.test.App"
    assert errors[0]["message"] == "Fail"


def test_parse_logs_with_bracketed_level_thread_method_pattern(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "bracketed.log"
    log_file.write_text(
        "[2025-06-26 20:20:48.082] [DEBUG] [SpringApplicationShutdownHook] "
        "o.a.h.i.c.PoolingHttpClientConnectionManager.shutdown(411) - "
        "Connection manager is shutting down\n"
        "[2025-06-26 20:20:48.083] [INFO ] [SpringApplicationShutdownHook] "
        "o.s.o.j.AbstractEntityManagerFactoryBean.destroy(650) - "
        "Closing JPA EntityManagerFactory for persistence unit 'default'\n"
    )

    counts, errors = parse_logs(
        str(d),
        log_pattern="[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n",
    )

    assert counts["DEBUG"] == 1
    assert counts["INFO"] == 1
    assert errors[0]["logger"] == "o.a.h.i.c.PoolingHttpClientConnectionManager"
    assert errors[1]["logger"] == "o.s.o.j.AbstractEntityManagerFactoryBean"


def test_parse_logs_does_not_compile_regex_on_each_call(temp_log_dir, monkeypatch):
    def fail_compile_logback_pattern(*_args, **_kwargs):
        raise AssertionError("default pattern should be compiled once at module import")

    monkeypatch.setattr(parser_module, "compile_logback_pattern", fail_compile_logback_pattern)
    
    class FakeDateTime:
        @staticmethod
        def strptime(value, fmt):
            return datetime(2026, 6, 6, 10, 0, 0)

    monkeypatch.setattr(parser_module, "datetime", FakeDateTime)

    counts, errors = parse_logs(temp_log_dir)

    assert counts['INFO'] == 2
    assert counts['ERROR'] == 1
    assert len(errors) == 3
