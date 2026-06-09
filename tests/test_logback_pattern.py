import pytest

from log_analyzer.domain.logback_pattern import (
    UnsupportedLogbackPatternError,
    compile_logback_pattern,
)


def test_compile_logback_pattern_matches_default_logback_shape():
    compiled = compile_logback_pattern(
        "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
    )

    match = compiled.match("2026-06-07 12:34:56.789 [main] INFO  com.test.App - Started")

    assert match is not None
    assert match.group("timestamp") == "2026-06-07 12:34:56.789"
    assert match.group("thread") == "main"
    assert match.group("level") == "INFO"
    assert match.group("logger") == "com.test.App"
    assert match.group("message") == "Started"


def test_compile_logback_pattern_matches_logback_default_comma_millis():
    compiled = compile_logback_pattern("%d %-5level [%thread] %logger{0}: %msg%n")

    match = compiled.match(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: "
        "Starting YtMamApp on ytmam_ap_test"
    )
    match_with_colon = compiled.match(
        "2026-05-25 15:50:33,214 INFO  [main] YtMamApp: "
        "The following profiles are active: swagger,dev"
    )

    assert match is not None
    assert match.group("timestamp") == "2026-05-25 15:50:33,198"
    assert match.group("level") == "INFO"
    assert match.group("thread") == "main"
    assert match.group("logger") == "YtMamApp"
    assert match.group("message") == "Starting YtMamApp on ytmam_ap_test"
    assert match_with_colon is not None
    assert match_with_colon.group("logger") == "YtMamApp"
    assert match_with_colon.group("message") == "The following profiles are active: swagger,dev"


def test_compile_logback_pattern_allows_file_and_line_before_message_separator():
    compiled = compile_logback_pattern(
        "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} %file:%line - %msg%n"
    )

    match = compiled.match("2026-06-07 12:34:56.789 INFO  [main] com.test.App App.java:42 - Started")

    assert match is not None
    assert match.group("logger") == "com.test.App"
    assert match.group("message") == "Started"


def test_compile_logback_pattern_rejects_unsupported_token():
    with pytest.raises(UnsupportedLogbackPatternError) as exc:
        compile_logback_pattern("%d{yyyy-MM-dd HH:mm:ss.SSS} %foo %n")

    assert "%foo" in str(exc.value)


def test_compile_logback_pattern_expands_spring_boot_file_pattern_defaults():
    compiled = compile_logback_pattern(
        "${FILE_LOG_PATTERN:-%d{${LOG_DATEFORMAT_PATTERN:-yyyy-MM-dd HH:mm:ss.SSS}} "
        "${LOG_LEVEL_PATTERN:-%5p}[%t][pid-${PID:- }][line-%line] "
        "%-40.40logger{39} : %m%n${LOG_EXCEPTION_CONVERSION_WORD:-%wEx}}"
    )

    match = compiled.match(
        "2026-06-07 12:34:56.789 ERROR[http-nio-8080-exec-1][pid- ][line-42] "
        "com.infotrends.api.Controller              : Request failed"
    )

    assert match is not None
    assert match.group("timestamp") == "2026-06-07 12:34:56.789"
    assert match.group("level") == "ERROR"
    assert match.group("thread") == "http-nio-8080-exec-1"
    assert match.group("logger") == "com.infotrends.api.Controller"
    assert match.group("message") == "Request failed"


def test_compile_logback_pattern_allows_spring_boot_throwable_tokens():
    compiled = compile_logback_pattern(
        "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n%wex"
    )

    match = compiled.match("2026-06-07 12:34:56.789 [main] ERROR com.test.App - Failed")

    assert match is not None
    assert match.group("message") == "Failed"


def test_compile_logback_pattern_matches_bracketed_level_thread_method_shape():
    compiled = compile_logback_pattern(
        "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n"
    )

    first = compiled.match(
        "[2025-06-26 20:20:48.082] [DEBUG] [SpringApplicationShutdownHook] "
        "o.a.h.i.c.PoolingHttpClientConnectionManager.shutdown(411) - "
        "Connection manager is shutting down"
    )
    second = compiled.match(
        "[2025-06-26 20:20:48.083] [INFO ] [SpringApplicationShutdownHook] "
        "o.s.o.j.AbstractEntityManagerFactoryBean.destroy(650) - "
        "Closing JPA EntityManagerFactory for persistence unit 'default'"
    )

    assert first is not None
    assert first.group("timestamp") == "2025-06-26 20:20:48.082"
    assert first.group("level") == "DEBUG"
    assert first.group("thread") == "SpringApplicationShutdownHook"
    assert first.group("logger") == "o.a.h.i.c.PoolingHttpClientConnectionManager"
    assert first.group("message") == "Connection manager is shutting down"

    assert second is not None
    assert second.group("level") == "INFO"
    assert second.group("logger") == "o.s.o.j.AbstractEntityManagerFactoryBean"
    assert second.group("message") == "Closing JPA EntityManagerFactory for persistence unit 'default'"


def test_compile_logback_pattern_supports_class_token_and_wrapper_converters():
    compiled = compile_logback_pattern(
        "%d{yyyy-MM-dd HH:mm:ss.SSS} %highlight(%-5level) [%thread] %C{36} - %replace(%msg){'x','y'}%n"
    )

    match = compiled.match("2026-06-07 12:34:56.789 INFO  [main] com.test.App - Started")

    assert match is not None
    assert match.group("timestamp") == "2026-06-07 12:34:56.789"
    assert match.group("level") == "INFO"
    assert match.group("thread") == "main"
    assert match.group("logger") == "com.test.App"
    assert match.group("message") == "Started"
