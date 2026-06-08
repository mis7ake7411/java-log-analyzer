from log_analyzer.domain.logback_xml import find_best_logback_pattern, load_logback_patterns


def test_load_logback_patterns_extracts_property_and_encoder_patterns(tmp_path):
    xml_file = tmp_path / "logback.xml"
    xml_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/>
    <appender name="FILE" class="ch.qos.logback.core.rolling.RollingFileAppender">
        <encoder>
            <Pattern>[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n</Pattern>
        </encoder>
    </appender>
</configuration>
""",
        encoding="utf-8",
    )

    candidates = load_logback_patterns(str(xml_file))

    assert [candidate.name for candidate in candidates] == [
        "FILE_LOG_PATTERN",
        "FILE Pattern",
    ]
    assert candidates[0].pattern == "%d %-5level [%thread] %logger{0}: %msg%n"


def test_find_best_logback_pattern_scores_candidates_against_log_samples(tmp_path):
    xml_file = tmp_path / "logback.xml"
    xml_file.write_text(
        """<configuration>
    <property name="CONSOLE_LOG_PATTERN" value="[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n"/>
    <property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/>
</configuration>
""",
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: Starting YtMamApp\n"
        "2026-05-25 15:50:33,198 DEBUG [main] YtMamApp: Running with Spring Boot\n",
        encoding="utf-8",
    )

    best = find_best_logback_pattern(str(xml_file), str(log_dir))

    assert best is not None
    assert best.name == "FILE_LOG_PATTERN"
    assert best.matches == 2
    assert best.checked == 2
