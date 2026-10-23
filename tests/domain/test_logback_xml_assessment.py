from pathlib import Path

import pytest

from log_analyzer.domain.logback_xml import (
    assess_logback_patterns,
    find_best_logback_pattern,
)


PATTERN = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n"
MATCHING_LINE = "2026-10-24 12:00:00.123 [main] INFO  com.test.App - sample"


def _write_xml(path: Path, *patterns: tuple[str, str]) -> Path:
    properties = "".join(
        f'<property name="{name}" value="{pattern}"/>'
        for name, pattern in patterns
    )
    path.write_text(f"<configuration>{properties}</configuration>", encoding="utf-8")
    return path


def _write_logs(directory: Path, *lines: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "app.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def test_assessment_selects_unique_pattern_at_exact_confidence_boundary(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", *(MATCHING_LINE for _ in range(4)), "not a log line")

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "ready"
    assert assessment.selected is assessment.candidates[0]
    assert assessment.selected.xml_path == str(xml_path)
    assert assessment.selected.matches == 4
    assert assessment.selected.checked == 5
    assert assessment.checked == 5
    assert assessment.selected.previews[0].raw == MATCHING_LINE
    assert assessment.selected.previews[0].fields["message"] == "sample"
    assert assessment.selected.previews[-1].fields is None


def test_unsupported_candidate_does_not_hide_a_valid_candidate(tmp_path):
    xml_path = _write_xml(
        tmp_path / "logback.xml",
        ("UNSUPPORTED_PATTERN", "%d [%thread] %-5level %logger - %msg %springProperty%n"),
        ("VALID_PATTERN", PATTERN),
    )
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "ready"
    assert assessment.selected.pattern == PATTERN
    unsupported = next(candidate for candidate in assessment.candidates if candidate.error)
    assert "%springProperty" in unsupported.error


def test_all_unsupported_candidates_are_not_selected(tmp_path):
    xml_path = _write_xml(
        tmp_path / "logback.xml",
        ("UNSUPPORTED_PATTERN", "%d [%thread] %-5level %logger - %msg %springProperty%n"),
    )
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "unsupported"
    assert assessment.selected is None
    assert assessment.candidates[0].error
    assert "不支援" in assessment.reason
    assert "UNSUPPORTED_PATTERN" in assessment.reason
    assert "%springProperty" in assessment.reason


def test_empty_xml_reports_no_candidates_as_unsupported(tmp_path):
    xml_path = tmp_path / "logback.xml"
    xml_path.write_text("<configuration />", encoding="utf-8")
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "unsupported"
    assert assessment.selected is None
    assert assessment.candidates == ()
    assert "找不到" in assessment.reason


def test_no_log_samples_is_uncertain(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "uncertain"
    assert assessment.checked == 0
    assert assessment.selected is None
    assert "樣本" in assessment.reason


def test_zero_matches_is_uncertain_and_legacy_lookup_returns_none(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", "arbitrary text")

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "uncertain"
    assert assessment.selected is None
    assert assessment.candidates[0].matches == 0
    assert find_best_logback_pattern(str(xml_path), str(log_dir)) is None


def test_low_confidence_is_uncertain(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(
        tmp_path / "logs",
        MATCHING_LINE,
        "not a log line",
        "not a log line",
        "not a log line",
        "not a log line",
    )

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "uncertain"
    assert assessment.selected is None
    assert assessment.candidates[0].score == pytest.approx(0.2)


def test_different_patterns_tied_for_top_matches_are_uncertain(tmp_path):
    xml_path = _write_xml(
        tmp_path / "logback.xml",
        ("FIRST_PATTERN", PATTERN),
        ("SECOND_PATTERN", PATTERN.replace("%msg", "%message")),
    )
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "uncertain"
    assert assessment.selected is None
    assert len(assessment.candidates) == 2
    assert all(candidate.matches == 1 for candidate in assessment.candidates)
    assert "平手" in assessment.reason


def test_same_pattern_across_xml_is_deduplicated_and_keeps_first_source(tmp_path):
    first = _write_xml(tmp_path / "first.xml", ("FIRST_PATTERN", PATTERN))
    second = _write_xml(tmp_path / "second.xml", ("SECOND_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(first), str(second)], str(log_dir))

    assert assessment.status == "ready"
    assert len(assessment.candidates) == 1
    assert assessment.selected.xml_path == str(first)


def test_different_patterns_tied_across_xml_remain_ambiguous(tmp_path):
    first = _write_xml(tmp_path / "first.xml", ("FIRST_PATTERN", PATTERN))
    second = _write_xml(
        tmp_path / "second.xml",
        ("SECOND_PATTERN", PATTERN.replace("%msg", "%message")),
    )
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(first), str(second)], str(log_dir))

    assert assessment.status == "uncertain"
    assert assessment.selected is None
    assert {candidate.xml_path for candidate in assessment.candidates} == {
        str(first),
        str(second),
    }


def test_assessment_uses_only_frozen_selected_files(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)
    (log_dir / "sibling.log").write_text("arbitrary text\n", encoding="utf-8")

    assessment = assess_logback_patterns(
        [str(xml_path)],
        str(log_dir),
        selected_files=("app.log",),
    )

    assert assessment.status == "ready"
    assert assessment.checked == 1
    assert assessment.selected.matches == 1


def test_preview_text_is_bounded_and_keeps_markup_like_text_literal(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    long_message = "[bold]" + ("x" * 700)
    log_dir = _write_logs(
        tmp_path / "logs",
        f"2026-10-24 12:00:00.123 [main] INFO  com.test.App - {long_message}",
    )

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    preview = assessment.selected.previews[0]
    assert "[bold]" in preview.raw
    assert len(preview.raw) <= 500
    assert all(len(value) <= 500 for value in preview.fields.values())
    assert preview.fields["message"].startswith("[bold]")


def test_assessment_samples_at_most_fifty_nonempty_lines(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", *(MATCHING_LINE for _ in range(51)))

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir), sample_limit=100)

    assert assessment.status == "ready"
    assert assessment.checked == 50
    assert assessment.selected.checked == 50


@pytest.mark.parametrize("sample_limit", [0, -1])
def test_assessment_rejects_nonpositive_sample_limit(tmp_path, sample_limit):
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    with pytest.raises(ValueError, match="sample_limit"):
        assess_logback_patterns([str(xml_path)], str(log_dir), sample_limit)
