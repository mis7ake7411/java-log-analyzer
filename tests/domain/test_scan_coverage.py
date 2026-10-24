from datetime import datetime

from log_analyzer.domain.parser import ParseOptions, parse_log_directory


def test_scan_coverage_classifies_lines_without_counting_stacktraces_as_failures(tmp_path):
    """情境：覆蓋診斷應正確分類各行且不把堆疊算作解析失敗。"""
    source = tmp_path / "app.log"
    source.write_text(
        "orphan before first event\n\n"
        "2026-10-24 10:00:00.000 [one] ERROR Service - Failed\n"
        "java.lang.IllegalStateException: failure\n"
        "\tat Service.run(Service.java:1)\n"
        "explanation on another line\n"
        "2026-10-24T11:00:00 INFO Other - different format\n",
        encoding="utf-8",
    )
    counts, store = parse_log_directory(str(tmp_path), ParseOptions())
    try:
        coverage = store.diagnostics.file_coverage[0]
        assert coverage.filename == "app.log"
        assert coverage.total_lines == 7
        assert coverage.parsed_entries == 1
        assert coverage.blank_lines == 1
        assert coverage.stacktrace_lines == 2
        assert coverage.continuation_lines == 1
        assert coverage.unrecognized_lines == 2
        assert coverage.unrecognized_samples == [
            (1, "orphan before first event"),
            (7, "2026-10-24T11:00:00 INFO Other - different format"),
        ]
        assert counts["ERROR"] == 1
        assert len(store) == 1
    finally:
        store.release()


def test_coverage_is_before_filters_and_examples_are_bounded(tmp_path):
    """情境：覆蓋計數應在篩選前建立且未知樣本數量受限。"""
    (tmp_path / "a.log").write_text("unknown\n" * 10, encoding="utf-8")
    (tmp_path / "b.log").write_text(
        "2026-10-24 10:00:00.000 [one] INFO Service - message\n"
        "continued message\n",
        encoding="utf-8",
    )
    _, store = parse_log_directory(
        str(tmp_path), ParseOptions(start_time=datetime(2026, 10, 25))
    )
    try:
        first, second = store.diagnostics.file_coverage
        assert first.unrecognized_lines == 10
        assert len(first.unrecognized_samples) == 3
        assert second.parsed_entries == 1
        assert second.continuation_lines == 1
        assert second.unrecognized_lines == 0
        assert store.diagnostics.after_time == 0
    finally:
        store.release()
