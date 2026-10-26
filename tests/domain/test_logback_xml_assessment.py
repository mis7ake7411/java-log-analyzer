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


def test_multifile_assessment_samples_beyond_the_first_large_file(tmp_path):
    """情境：多檔評估不應只對第一個大檔抽樣。"""
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").write_text((MATCHING_LINE + "\n") * 100, encoding="utf-8")
    (logs / "b.log").write_text("different format\n" * 100, encoding="utf-8")
    assessment = assess_logback_patterns([str(xml_path)], str(logs))
    assert assessment.checked == 50
    assert assessment.candidates[0].matches == 25
    assert assessment.status == "uncertain"
    assert assessment.selected is None
    assert [(item.filename, item.matches, item.checked)
            for item in assessment.candidates[0].file_scores] == [
        ("a.log", 25, 25), ("b.log", 0, 25),
    ]


def test_multifile_sampling_reuses_unused_budget_for_later_files(tmp_path):
    """情境：前檔未用完的抽樣額度應讓後續檔案使用。"""
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").write_text("\n\n", encoding="utf-8")
    (logs / "b.log").write_text((MATCHING_LINE + "\n") * 60, encoding="utf-8")
    assessment = assess_logback_patterns([str(xml_path)], str(logs))
    assert assessment.checked == 50
    assert assessment.candidates[0].matches == 50
    assert assessment.status == "ready"


def test_good_total_score_does_not_hide_one_incompatible_file(tmp_path):
    """情境：總分良好時仍須揭露某個檔案與格式不相容。"""
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    logs = tmp_path / "logs"
    logs.mkdir()
    for index in range(10):
        content = MATCHING_LINE if index < 9 else "different format"
        (logs / f"{index}.log").write_text((content + "\n") * 10, encoding="utf-8")
    assessment = assess_logback_patterns([str(xml_path)], str(logs))
    assert assessment.candidates[0].score == pytest.approx(0.9)
    assert assessment.status == "uncertain"
    assert "9.log" in assessment.reason


def test_assessment_selects_unique_pattern_at_exact_confidence_boundary(tmp_path):
    """情境：剛好達到可信門檻的唯一候選應可選定。"""
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
    """情境：存在不支援候選時仍可選出有效候選。"""
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
    """情境：全部候選皆不支援時不得自動選定。"""
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
    """情境：空 XML 應回報沒有候選而不是使用內建格式。"""
    xml_path = tmp_path / "logback.xml"
    xml_path.write_text("<configuration />", encoding="utf-8")
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "unsupported"
    assert assessment.selected is None
    assert assessment.candidates == ()
    assert "找不到" in assessment.reason


def test_no_log_samples_is_uncertain(tmp_path):
    """情境：沒有日誌樣本時評估應為不確定。"""
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "uncertain"
    assert assessment.checked == 0
    assert assessment.selected is None
    assert "樣本" in assessment.reason


def test_zero_matches_is_uncertain_and_legacy_lookup_returns_none(tmp_path):
    """情境：零匹配應回報不確定且舊介面回傳 None。"""
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", "arbitrary text")

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))

    assert assessment.status == "uncertain"
    assert assessment.selected is None
    assert assessment.candidates[0].matches == 0
    assert find_best_logback_pattern(str(xml_path), str(log_dir)) is None


def test_low_confidence_is_uncertain(tmp_path):
    """情境：低於可信門檻的評分應為不確定。"""
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
    """情境：不同 Pattern 同為最高分時應要求確認。"""
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
    """情境：多份 XML 的相同 Pattern 應去重並保留首次來源。"""
    first = _write_xml(tmp_path / "first.xml", ("FIRST_PATTERN", PATTERN))
    second = _write_xml(tmp_path / "second.xml", ("SECOND_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    assessment = assess_logback_patterns([str(first), str(second)], str(log_dir))

    assert assessment.status == "ready"
    assert len(assessment.candidates) == 1
    assert assessment.selected.xml_path == str(first)


def test_different_patterns_tied_across_xml_remain_ambiguous(tmp_path):
    """情境：跨 XML 的不同 Pattern 同分時仍應視為歧義。"""
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
    """情境：評估只能使用本輪固定的已選檔案。"""
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
    """情境：樣本文字應限制長度且將標記語法當成原文。"""
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
    """情境：抽樣不得超過五十個非空行。"""
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", *(MATCHING_LINE for _ in range(51)))

    assessment = assess_logback_patterns([str(xml_path)], str(log_dir), sample_limit=100)

    assert assessment.status == "ready"
    assert assessment.checked == 50
    assert assessment.selected.checked == 50


@pytest.mark.parametrize("sample_limit", [0, -1], ids=['zero-sample-limit', 'negative-sample-limit'])
def test_assessment_rejects_nonpositive_sample_limit(tmp_path, sample_limit):
    """情境：零或負抽樣上限應拒絕。"""
    xml_path = _write_xml(tmp_path / "logback.xml", ("LOG_PATTERN", PATTERN))
    log_dir = _write_logs(tmp_path / "logs", MATCHING_LINE)

    with pytest.raises(ValueError, match="sample_limit"):
        assess_logback_patterns([str(xml_path)], str(log_dir), sample_limit)
