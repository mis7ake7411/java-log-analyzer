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


def test_parse_logs_defaults_to_time_order(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "ordered.log"
    log_file.write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Info first\n"
        "2026-06-06 10:01:00.000 [main] ERROR com.test - Error second\n"
        "2026-06-06 10:02:00.000 [main] WARN  com.test - Warn third\n"
    )

    _counts, errors = parse_logs(str(d))

    assert [entry["message"] for entry in errors] == [
        "Info first",
        "Error second",
        "Warn third",
    ]


def test_parse_logs_can_sort_by_level_then_time(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "levels.log"
    log_file.write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Info first\n"
        "2026-06-06 10:01:00.000 [main] ERROR com.test - Error later\n"
        "2026-06-06 10:02:00.000 [main] WARN  com.test - Warn later\n"
        "2026-06-06 10:03:00.000 [main] DEBUG com.test - Debug later\n"
        "2026-06-06 09:59:00.000 [main] ERROR com.test - Error earlier\n"
    )

    _counts, errors = parse_logs(str(d), sort_by="level")

    assert [(entry["level"], entry["message"]) for entry in errors] == [
        ("ERROR", "Error earlier"),
        ("ERROR", "Error later"),
        ("WARN", "Warn later"),
        ("INFO", "Info first"),
        ("DEBUG", "Debug later"),
    ]


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


def test_parse_logs_with_logback_default_comma_millis_pattern(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "logFile.2026-05-25.log"
    log_file.write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: "
        "Starting YtMamApp on ytmam_ap_test\n"
        "2026-05-25 15:50:33,198 DEBUG [main] YtMamApp: "
        "Running with Spring Boot v2.2.7.RELEASE\n"
        "2026-05-25 15:50:33,214 INFO  [main] YtMamApp: "
        "The following profiles are active: swagger,dev\n"
    )

    counts, errors = parse_logs(
        str(d),
        log_pattern="%d %-5level [%thread] %logger{0}: %msg%n",
    )

    assert counts["INFO"] == 2
    assert counts["DEBUG"] == 1
    assert errors[0]["timestamp"] == "2026-05-25 15:50:33,198"
    assert errors[0]["logger"] == "YtMamApp"
    assert errors[0]["message"] == "Starting YtMamApp on ytmam_ap_test"
    assert errors[2]["logger"] == "YtMamApp"
    assert errors[2]["message"] == "The following profiles are active: swagger,dev"


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
