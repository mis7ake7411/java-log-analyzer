from __future__ import annotations

from log_analyzer.domain.logback.pattern import compile_logback_pattern
from log_analyzer.domain.logback.xml import preview_logback_pattern
from log_analyzer.domain.parser import parse_logs
from log_analyzer.domain.logback.mdc import extract_mdc


BASE = "%d{yyyy-MM-dd HH:mm:ss.SSS}"
TAIL = " %-5level [%thread] %logger{0}: %msg%n"


def _match(pattern: str, rendered_fields: str):
    compiled = compile_logback_pattern(BASE + " " + pattern + TAIL)
    return compiled.match(
        f"2026-06-06 10:00:00.001 {rendered_fields} "
        "INFO  [main] com.test: Shared message"
    )


def test_extract_mdc_supports_keyed_aliases_unicode_keys_and_defaults():
    pattern = "%X{traceId:-missing}|%mdc{租戶:-未知}|%X{UserID}|%mdc{userid}"
    match = _match(pattern, "trace-7|租戶甲|CaseSensitive|case-sensitive")

    assert match is not None
    assert extract_mdc(match) == {
        "traceId": "trace-7",
        "租戶": "租戶甲",
        "UserID": "CaseSensitive",
        "userid": "case-sensitive",
    }


def test_extract_mdc_omits_empty_and_default_substitutions():
    match = _match("%X{traceId:-missing}|%mdc{userId}", "missing|")

    assert match is not None
    assert extract_mdc(match) == {}


def test_extract_mdc_accepts_equal_repeated_values_and_drops_conflicts():
    pattern = "%X{traceId}|%mdc{traceId}"
    equal = _match(pattern, "same|same")
    conflicting = _match(pattern, "first|second")

    assert equal is not None and extract_mdc(equal) == {"traceId": "same"}
    assert conflicting is not None and extract_mdc(conflicting) == {}


def test_extract_mdc_parses_bare_map_and_rejects_malformed_map():
    valid = _match("%X", "traceId=abc, user=alice")
    malformed = _match("%mdc", "traceId=abc, broken")
    conflict = _match("%X", "traceId=abc, user=alice, traceId=other")

    assert valid is not None
    assert extract_mdc(valid) == {"traceId": "abc", "user": "alice"}
    assert malformed is not None and extract_mdc(malformed) == {}
    assert conflict is not None
    assert extract_mdc(conflict) == {"user": "alice"}


def test_extract_mdc_trims_pattern_padding_and_omits_max_width_truncation():
    right_aligned = _match("%8X{traceId}", "     abc")
    left_aligned = _match("%-8X{traceId}", "abc     ")
    short_with_max = _match("%-8.5X{traceId}", "abc     ")
    truncated = _match("%-8.5X{traceId}", "abcde   ")
    short_bare_map = _match("%.12X", "traceId=abc")
    truncated_bare_map = _match("%.12X", "traceId=1234")

    assert right_aligned is not None
    assert extract_mdc(right_aligned) == {"traceId": "abc"}
    assert left_aligned is not None
    assert extract_mdc(left_aligned) == {"traceId": "abc"}
    assert short_with_max is not None
    assert extract_mdc(short_with_max) == {"traceId": "abc"}
    assert truncated is not None and extract_mdc(truncated) == {}
    assert short_bare_map is not None
    assert extract_mdc(short_bare_map) == {"traceId": "abc"}
    assert truncated_bare_map is not None
    assert extract_mdc(truncated_bare_map) == {}


def test_mdc_capture_names_are_unique_and_regex_safe():
    compiled = compile_logback_pattern(
        BASE + " %X{traceId} %mdc{租戶} %X{traceId}" + TAIL
    )
    mdc_names = [name for name in compiled.groupindex if name.startswith("mdc_")]

    assert len(mdc_names) == 3
    assert len(set(mdc_names)) == 3
    assert all(name.replace("_", "").isalnum() for name in mdc_names)


def test_pattern_preview_exposes_decoded_mdc_fields_not_internal_group_names(tmp_path):
    log_file = tmp_path / "preview.log"
    log_file.write_text(
        "2026-06-06 10:00:00.001 trace-7 INFO  [main] com.test: Preview\n",
        encoding="utf-8",
    )

    candidate = preview_logback_pattern(
        BASE + " %X{traceId}" + TAIL,
        str(log_file),
    )

    assert candidate.previews[0].fields is not None
    assert candidate.previews[0].fields["MDC.traceId"] == "trace-7"
    assert not any(name.startswith("mdc_") for name in candidate.previews[0].fields)


def test_cross_file_duplicate_message_keeps_each_occurrence_mdc_and_round_trips(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    pattern = BASE + " %X{traceId}" + TAIL
    (log_dir / "a.log").write_text(
        "2026-06-06 10:00:00.001 trace-A INFO  [worker-a] com.test: Shared message\n",
        encoding="utf-8",
    )
    (log_dir / "b.log").write_text(
        "2026-06-06 10:01:00.002 trace-B INFO  [worker-b] com.test: Shared message\n",
        encoding="utf-8",
    )

    _counts, groups = parse_logs(str(log_dir), log_pattern=pattern)

    assert len(groups) == 1
    group = groups[0]
    assert group["count"] == 2
    assert [occurrence["mdc"] for occurrence in group["occurrences"]] == [
        {"traceId": "trace-A"},
        {"traceId": "trace-B"},
    ]
    group["occurrences"][0]["mdc"]["traceId"] = "changed"
    assert group["occurrences"][1]["mdc"] == {"traceId": "trace-B"}
    persisted = group.to_dict()
    persisted["occurrences"][0]["mdc"]["traceId"] = "changed"
    assert group["occurrences"][0]["mdc"] == {"traceId": "changed"}


def test_no_mdc_parse_shape_remains_unchanged(tmp_path):
    log_file = tmp_path / "plain.log"
    log_file.write_text(
        "2026-06-06 10:00:00.001 [main] INFO  com.test - Plain message\n",
        encoding="utf-8",
    )

    _counts, groups = parse_logs(str(log_file))
    group = groups[0]

    assert "mdc" not in group["occurrences"][0]
    assert "mdc" not in group.to_dict()["occurrences"][0]
