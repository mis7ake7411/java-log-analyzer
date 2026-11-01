from pathlib import Path

import pytest

import log_analyzer.domain.logback.xml as logback_xml
from log_analyzer.domain.logback.pattern import (
    DEFAULT_LOGBACK_REGEX,
    compile_logback_pattern,
)


DEFAULT_LINE = "2026-10-24 12:00:00.123 [main] INFO  com.test.App - Started"
DEFAULT_PATTERN = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"


def _preview(*args, **kwargs):
    preview = getattr(logback_xml, "preview_logback_pattern", None)
    assert callable(preview), "logback_xml.preview_logback_pattern 尚未提供"
    return preview(*args, **kwargs)


def _write_logs(directory: Path, *lines: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "app.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def test_preview_default_pattern_returns_default_named_fields(tmp_path):
    """情境：未指定 Pattern 時應使用正式內建解析器並回傳具名欄位。"""
    logs = _write_logs(tmp_path / "logs", DEFAULT_LINE)

    candidate = _preview(None, str(logs))

    assert candidate.pattern == ""
    assert candidate.source == "preview"
    assert candidate.matches == candidate.checked == 1
    assert candidate.previews[0].fields == {
        "timestamp": "2026-10-24 12:00:00.123",
        "thread": "main",
        "level": "INFO",
        "logger": "com.test.App",
        "message": "Started",
    }
    assert compile_logback_pattern(candidate.pattern) is DEFAULT_LOGBACK_REGEX


def test_preview_custom_pattern_returns_its_named_fields(tmp_path):
    """情境：自訂 Pattern 應依各欄位名稱回傳解析結果。"""
    custom_pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger - %msg%n"
    custom_line = "2026-10-24 12:00:00.123 WARN  [worker-2] com.demo.Worker - Updated"
    logs = _write_logs(tmp_path / "logs", custom_line)

    candidate = _preview(custom_pattern, str(logs))

    assert candidate.pattern == custom_pattern
    assert candidate.matches == candidate.checked == 1
    assert candidate.previews[0].fields == {
        "timestamp": "2026-10-24 12:00:00.123",
        "level": "WARN",
        "thread": "worker-2",
        "logger": "com.demo.Worker",
        "message": "Updated",
    }


def test_preview_nonmatching_line_has_no_extracted_fields(tmp_path):
    """情境：未符合 Pattern 的原始日誌列應保留但沒有欄位。"""
    logs = _write_logs(tmp_path / "logs", "not a log line")

    candidate = _preview(DEFAULT_PATTERN, str(logs))

    assert candidate.matches == 0
    assert candidate.checked == 1
    assert candidate.previews[0].raw == "not a log line"
    assert candidate.previews[0].fields is None


def test_preview_keeps_unsupported_token_error_on_candidate(tmp_path):
    """情境：不支援的 token 應留在候選錯誤欄位供畫面呈現。"""
    logs = _write_logs(tmp_path / "logs", DEFAULT_LINE)
    pattern = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level %logger - %msg %springProperty%n"

    candidate = _preview(pattern, str(logs))

    assert candidate.error is not None
    assert "%springProperty" in candidate.error
    assert candidate.matches == 0
    assert candidate.checked == 1


def test_preview_caps_requested_sample_count_at_fifty(tmp_path):
    """情境：即使要求大量抽樣也不得超過既有五十筆上限。"""
    logs = _write_logs(tmp_path / "logs", *(DEFAULT_LINE for _ in range(75)))

    candidate = _preview(DEFAULT_PATTERN, str(logs), sample_limit=10_000)

    assert candidate.checked == 50
    assert candidate.matches == 50


def test_preview_reads_only_explicitly_selected_file(tmp_path):
    """情境：明確選取單一檔案時不得把同資料夾的其他檔案納入抽樣。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "selected.log").write_text(DEFAULT_LINE + "\n", encoding="utf-8")
    (logs / "sibling.log").write_text("different sibling data\n" * 20, encoding="utf-8")

    candidate = _preview(DEFAULT_PATTERN, str(logs), selected_files=("selected.log",))

    assert candidate.checked == 1
    assert candidate.matches == 1
    assert [(score.filename, score.checked) for score in candidate.file_scores] == [
        ("selected.log", 1),
    ]


def test_preview_distinguishes_empty_selection_from_default_discovery(tmp_path):
    """情境：空選取代表不讀取檔案，None 則使用來源預設發現結果。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text(DEFAULT_LINE + "\n", encoding="utf-8")

    discovered = _preview(DEFAULT_PATTERN, str(logs), selected_files=None)
    empty = _preview(DEFAULT_PATTERN, str(logs), selected_files=())

    assert discovered.checked == 1
    assert [score.filename for score in discovered.file_scores] == ["app.log"]
    assert empty.checked == 0
    assert empty.file_scores == ()
    assert empty.previews == ()


@pytest.mark.parametrize(
    ("pattern", "sample_limit"),
    [(DEFAULT_PATTERN, 0), (" \t ", 1)],
    ids=["non-positive-sample-limit", "blank-pattern"],
)
def test_preview_rejects_blank_pattern_or_non_positive_limit(
    tmp_path, pattern, sample_limit
):
    """情境：空白 Pattern 與非正抽樣數應明確拒絕。"""
    logs = _write_logs(tmp_path / "logs", DEFAULT_LINE)

    with pytest.raises(ValueError):
        _preview(pattern, str(logs), sample_limit=sample_limit)


def test_preview_uses_utf8_replacement_for_malformed_source_bytes(tmp_path):
    """情境：來源含無效 UTF-8 時預覽應顯示替代字元而不中斷。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_bytes(
        b"2026-10-24 12:00:00.123 [main] INFO  com.test.App - Start\xffed\n"
    )

    candidate = _preview(None, str(logs))

    assert candidate.matches == 1
    assert candidate.previews[0].fields["message"] == "Start\ufffded"
    assert "\ufffd" in candidate.previews[0].raw


def test_preview_limits_previews_and_truncates_long_text(tmp_path):
    """情境：欄位預覽最多各三筆，長欄位與原文最多保留五百字元。"""
    long_line = DEFAULT_LINE.removesuffix("Started") + ("x" * 600)
    logs = _write_logs(tmp_path / "logs", *([long_line] * 4 + ["not a log line"] * 4))

    candidate = _preview(DEFAULT_PATTERN, str(logs))

    assert len(candidate.previews) == 6
    assert all(sample.fields is not None for sample in candidate.previews[:3])
    assert all(sample.fields is None for sample in candidate.previews[3:])
    assert len(candidate.previews[0].raw) == 500
    assert len(candidate.previews[0].fields["message"]) == 500


def test_preview_does_not_modify_source_file(tmp_path):
    """情境：建立欄位預覽不得改寫來源日誌。"""
    logs = _write_logs(tmp_path / "logs", DEFAULT_LINE)
    log_file = logs / "app.log"
    original = log_file.read_bytes()

    _preview(None, str(logs))

    assert log_file.read_bytes() == original
