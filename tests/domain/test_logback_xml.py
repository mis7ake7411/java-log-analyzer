from pathlib import Path

import pytest

from log_analyzer.domain.logback_xml import (
    _iter_nonempty_lines,
    find_best_logback_pattern,
    load_logback_patterns,
)


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "logback"


def test_log_sample_reader_replaces_invalid_utf8_instead_of_dropping_bytes(tmp_path):
    """情境：XML 評分樣本讀取遇無效 UTF-8 應替代而非丟棄位元組。"""
    sample = tmp_path / "sample.log"
    sample.write_bytes(b"valid \xef\xbf\xbd and invalid \x80\n")

    assert list(_iter_nonempty_lines(str(sample))) == [
        "valid \ufffd and invalid \ufffd\n"
    ]


def test_load_logback_patterns_extracts_property_and_encoder_patterns(tmp_path):
    """情境：XML 應擷取 property 與 encoder 中的 Pattern。"""
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
    """情境：候選 Pattern 應依日誌樣本匹配分數選擇。"""
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


def test_find_best_logback_pattern_scores_only_selected_log_samples(tmp_path):
    """情境：候選評分只能使用已選取的日誌樣本。"""
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
    (log_dir / "chosen.log").write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: Starting YtMamApp\n",
        encoding="utf-8",
    )
    (log_dir / "sibling.log").write_text(
        "[2026-06-06 10:00:00.001] [INFO ] [main] com.test.App.run(12) - sibling one\n"
        "[2026-06-06 10:00:00.002] [INFO ] [main] com.test.App.run(13) - sibling two\n"
        "[2026-06-06 10:00:00.003] [INFO ] [main] com.test.App.run(14) - sibling three\n",
        encoding="utf-8",
    )

    try:
        best = find_best_logback_pattern(
            str(xml_file),
            str(log_dir),
            selected_files=("chosen.log",),
        )
    except TypeError as error:
        pytest.fail(f"XML sampling should accept a frozen file selection: {error}")

    assert best is not None
    assert best.name == "FILE_LOG_PATTERN"
    assert best.matches == 1
    assert best.checked == 1


def test_find_best_logback_pattern_accepts_single_file_and_excludes_siblings(tmp_path):
    """情境：單檔來源的候選評分不得混入同層其他檔案。"""
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
    selected_file = log_dir / "chosen.log"
    selected_file.write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: Starting YtMamApp\n",
        encoding="utf-8",
    )
    (log_dir / "sibling.log").write_text(
        "[2026-06-06 10:00:00.001] [INFO ] [main] com.test.App.run(12) - sibling one\n"
        "[2026-06-06 10:00:00.002] [INFO ] [main] com.test.App.run(13) - sibling two\n"
        "[2026-06-06 10:00:00.003] [INFO ] [main] com.test.App.run(14) - sibling three\n",
        encoding="utf-8",
    )

    best = find_best_logback_pattern(str(xml_file), str(selected_file))

    assert best is not None
    assert best.name == "FILE_LOG_PATTERN"
    assert best.matches == 1
    assert best.checked == 1


def test_fixture_patterns_keep_candidate_order_and_metadata():
    """情境：測試 XML 的候選順序與來源中繼資料應保持。"""
    candidates = load_logback_patterns(str(FIXTURE_DIR / "standard-logback.xml"))

    assert [(candidate.name, candidate.pattern, candidate.source) for candidate in candidates] == [
        (
            "LOG_PATTERN",
            "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n",
            "property",
        ),
        (
            "FILE Pattern",
            "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n",
            "encoder",
        ),
    ]


def test_fixture_best_pattern_matches_standard_log_samples(tmp_path):
    """情境：標準日誌樣本應選出對應的最佳 Pattern。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        (FIXTURE_DIR / "standard-app.log").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    best = find_best_logback_pattern(
        str(FIXTURE_DIR / "standard-logback.xml"),
        str(log_dir),
    )

    assert best is not None
    assert best.name == "LOG_PATTERN"
    assert best.matches == 2
    assert best.checked == 2
