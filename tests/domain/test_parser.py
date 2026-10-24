import pytest
import pickle
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from log_analyzer.domain.parser import (
    MatchedLogsStore,
    ParseOptions,
    iter_logs,
    parse_log_directory,
    parse_logs,
)
import log_analyzer.domain.parser as parser_module
from log_analyzer.domain.log_types import ScanDiagnostics


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "logback"
STANDARD_LOG_PATTERN = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"

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
    counts, _errors = parse_logs(temp_log_dir)
    assert counts['INFO'] == 2
    assert counts['ERROR'] == 1


def test_parse_logs_matches_standard_logback_fixtures(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        (FIXTURE_DIR / "standard-app.log").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    counts, entries = parse_logs(
        str(log_dir),
        log_pattern=STANDARD_LOG_PATTERN,
    )

    assert counts == {"INFO": 1, "ERROR": 1}
    assert [entry["message"] for entry in entries] == [
        "Started application",
        "Failed to process request",
    ]


def test_iter_logs_discovers_supported_extensions_case_insensitively(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for filename, message in (
        ("a.LOG", "uppercase extension"),
        ("b.txt", "text extension"),
        ("c.OUT", "output extension"),
    ):
        (log_dir / filename).write_text(
            f"2026-06-06 10:00:00.001 INFO  [main] com.test.App: {message}\n",
            encoding="utf-8",
        )
    (log_dir / "ignored.json").write_text("unsupported\n", encoding="utf-8")
    (log_dir / "directory.log").mkdir()
    nested = log_dir / "nested"
    nested.mkdir()
    (nested / "inside.log").write_text(
        "2026-06-06 10:00:00.001 INFO  [main] com.test.App: nested\n",
        encoding="utf-8",
    )

    try:
        entries = list(iter_logs(str(log_dir), log_pattern=STANDARD_LOG_PATTERN))
    except OSError as error:
        pytest.fail(f"directory discovery should skip non-file entries: {error}")

    assert [entry["message"] for entry in entries] == [
        "uppercase extension",
        "text extension",
        "output extension",
    ]
    assert {entry["filename"] for entry in entries} == {"a.LOG", "b.txt", "c.OUT"}


def test_iter_logs_uses_explicit_selection_without_expanding_it(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "chosen.txt").write_text(
        "2026-06-06 10:00:00.001 INFO  [main] com.test.App: chosen\n",
        encoding="utf-8",
    )
    (log_dir / "sibling.log").write_text(
        "2026-06-06 10:00:00.001 ERROR [main] com.test.App: sibling\n",
        encoding="utf-8",
    )

    try:
        selected = list(
            iter_logs(
                str(log_dir),
                log_pattern=STANDARD_LOG_PATTERN,
                selected_files=("chosen.txt",),
            )
        )
        empty = list(
            iter_logs(
                str(log_dir),
                log_pattern=STANDARD_LOG_PATTERN,
                selected_files=(),
            )
        )
    except TypeError as error:
        pytest.fail(f"iter_logs should accept a frozen file selection: {error}")

    assert [entry["message"] for entry in selected] == ["chosen"]
    assert empty == []


def test_iter_logs_accepts_a_single_file_without_reading_its_sibling(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    selected_file = log_dir / "chosen.txt"
    selected_file.write_text(
        "2026-06-06 10:00:00.001 INFO  [main] com.test.App: chosen\n",
        encoding="utf-8",
    )
    (log_dir / "sibling.log").write_text(
        "2026-06-06 10:00:00.001 ERROR [main] com.test.App: sibling\n",
        encoding="utf-8",
    )

    entries = list(iter_logs(str(selected_file), log_pattern=STANDARD_LOG_PATTERN))

    assert [entry["message"] for entry in entries] == ["chosen"]
    assert {entry["filename"] for entry in entries} == {"chosen.txt"}


def test_parse_logs_filters_selected_levels(temp_log_dir):
    counts, errors = parse_logs(temp_log_dir, levels=("ERROR",))

    assert counts["ERROR"] == 1
    assert counts["INFO"] == 0
    assert len(errors) == 1
    assert errors[0]["level"] == "ERROR"


def test_parse_logs_compacts_single_line_numbers_and_expands_on_duplicates(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "duplicate.log"
    log_file.write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Same message\n"
        "2026-06-06 10:01:00.000 [main] INFO  com.test - Same message\n",
        encoding="utf-8",
    )

    _counts, errors = parse_logs(str(d))

    assert errors[0]["line_numbers"] == [1, 2]


def test_cross_file_group_keeps_each_occurrence_coordinate(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "a.log").write_text(
        "2026-06-06 10:00:00.001 [worker-a] INFO  com.test - Shared message\n",
        encoding="utf-8",
    )
    (log_dir / "b.log").write_text(
        "2026-06-06 10:01:00.002 [worker-b] INFO  com.test - Shared message\n",
        encoding="utf-8",
    )

    _counts, groups = parse_logs(str(log_dir))

    assert len(groups) == 1
    group = groups[0]
    assert group["count"] == len(group["occurrences"]) == 2
    assert group["occurrences"] == [
        {
            "filename": "a.log",
            "line_number": 1,
            "timestamp": "2026-06-06 10:00:00.001",
            "thread": "worker-a",
        },
        {
            "filename": "b.log",
            "line_number": 1,
            "timestamp": "2026-06-06 10:01:00.002",
            "thread": "worker-b",
        },
    ]
    assert group.to_dict()["occurrences"] == group["occurrences"]


def test_parse_logs_keyword(temp_log_dir):
    # Test keyword filtering
    counts, errors = parse_logs(temp_log_dir, keyword="RuntimeException")
    assert counts['INFO'] == 0 # INFO lines don't have this keyword
    assert counts['ERROR'] == 1
    assert len(errors) == 1


def test_parse_log_directory_matches_legacy_parse_logs(temp_log_dir):
    options = ParseOptions(
        start_time=datetime(2026, 6, 6, 10, 5),
        end_time=datetime(2026, 6, 6, 10, 15),
        keyword="runtimeexception",
        ignore_case=True,
        log_pattern="%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n",
        sort_by="level",
        levels=("ERROR",),
    )

    expected_counts, expected_logs = parse_logs(
        temp_log_dir,
        start_time=options.start_time,
        end_time=options.end_time,
        keyword=options.keyword,
        ignore_case=options.ignore_case,
        log_pattern=options.log_pattern,
        sort_by=options.sort_by,
        levels=options.levels,
    )
    actual_counts, actual_logs = parse_log_directory(temp_log_dir, options)

    assert actual_counts == expected_counts
    assert isinstance(actual_logs, MatchedLogsStore)
    assert list(actual_logs) == list(expected_logs)


def test_parse_log_directory_reports_zero_result_scan_diagnostics(tmp_path):
    log_dir = tmp_path / "empty-logs"
    log_dir.mkdir()

    counts, matched_logs = parse_log_directory(str(log_dir), ParseOptions())

    assert counts == {}
    assert list(matched_logs) == []
    assert matched_logs.diagnostics.files_scanned == 0
    assert matched_logs.diagnostics.parsed_entries == 0
    assert matched_logs.diagnostics.after_time == 0
    assert matched_logs.diagnostics.after_level == 0
    assert matched_logs.diagnostics.after_keyword == 0


def test_scan_diagnostics_appends_encoding_counts_without_reordering_existing_fields():
    diagnostics = ScanDiagnostics(1, 2, 3, 4, 5, 6, 7)

    assert (
        diagnostics.files_scanned,
        diagnostics.parsed_entries,
        diagnostics.after_time,
        diagnostics.after_level,
        diagnostics.after_keyword,
    ) == (1, 2, 3, 4, 5)
    assert diagnostics.encoding_error_files == 6
    assert diagnostics.encoding_error_lines == 7


def test_parser_replaces_invalid_utf8_in_matched_unmatched_and_stack_lines(tmp_path):
    log_dir = tmp_path / "lossy-utf8"
    log_dir.mkdir()
    source = log_dir / "mixed.log"
    source.write_bytes(
        b"\xef\xbb\xbf"
        b"2026-06-06 10:00:00.000 [main] INFO  com.test - valid \xef\xbf\xbd bad \x80\n"
        b"unmatched \x81\n"
        b"java.lang.RuntimeException: failure \x82\n"
        b"    at com.test.App.main \x83\n"
    )

    counts, matched_logs = parse_log_directory(str(log_dir), ParseOptions())
    try:
        assert counts == {"INFO": 1}
        assert len(matched_logs) == 1
        entry = matched_logs[0]
        assert entry["message"] == "valid \ufffd bad \ufffd"
        assert "unmatched \ufffd" in entry["message_body"]
        assert "java.lang.RuntimeException: failure \ufffd" in entry["stacktrace"]
        assert "at com.test.App.main \ufffd" in entry["stacktrace"]
        assert matched_logs.diagnostics.encoding_error_files == 1
        assert matched_logs.diagnostics.encoding_error_lines == 4
    finally:
        matched_logs.release()


def test_parse_log_directory_counts_unparseable_log_files(tmp_path):
    log_dir = tmp_path / "unparseable-logs"
    log_dir.mkdir()
    (log_dir / "broken.log").write_text("not a log entry\n", encoding="utf-8")
    (log_dir / "ignored.json").write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Ignored\n",
        encoding="utf-8",
    )

    counts, matched_logs = parse_log_directory(str(log_dir), ParseOptions())

    assert counts == {}
    assert list(matched_logs) == []
    assert matched_logs.diagnostics.files_scanned == 1
    assert matched_logs.diagnostics.parsed_entries == 0
    assert matched_logs.diagnostics.after_time == 0
    assert matched_logs.diagnostics.after_level == 0
    assert matched_logs.diagnostics.after_keyword == 0


def test_parse_log_directory_counts_events_through_sequential_filters(tmp_path):
    log_dir = tmp_path / "filtered-logs"
    log_dir.mkdir()
    (log_dir / "events.log").write_text(
        "2026-06-06 09:59:00.000 [main] INFO  com.test - Before range\n"
        "    before-range continuation\n"
        "2026-06-06 10:00:00.000 [main] DEBUG com.test - needle wrong level\n"
        "2026-06-06 10:01:00.000 [main] INFO  com.test - No keyword match\n"
        "    ordinary continuation\n"
        "2026-06-06 10:02:00.000 [main] ERROR com.test - Needle matched\n"
        "java.lang.RuntimeException: failure\n"
        "    at com.test.App.main\n"
        "2026-06-06 10:04:00.000 [main] ERROR com.test - Needle after range\n"
        "not a log entry\n",
        encoding="utf-8",
    )

    counts, matched_logs = parse_log_directory(
        str(log_dir),
        ParseOptions(
            start_time=datetime(2026, 6, 6, 10, 0),
            end_time=datetime(2026, 6, 6, 10, 3),
            keyword="needle",
            ignore_case=True,
            levels=("INFO", "ERROR"),
        ),
    )

    assert counts == {"ERROR": 1}
    assert [entry["message"] for entry in matched_logs] == ["Needle matched"]
    assert matched_logs.diagnostics.files_scanned == 1
    assert matched_logs.diagnostics.parsed_entries == 5
    assert matched_logs.diagnostics.after_time == 3
    assert matched_logs.diagnostics.after_level == 2
    assert matched_logs.diagnostics.after_keyword == 1
    assert len(matched_logs[0]["stacktrace"].splitlines()) == 2


def test_parse_log_directory_preserves_diagnostics_across_group_spills(tmp_path, monkeypatch):
    log_dir = tmp_path / "spilled-logs"
    log_dir.mkdir()
    (log_dir / "spill.log").write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Same message\n"
        "2026-06-06 10:01:00.000 [main] INFO  com.test - Same message\n"
        "2026-06-06 10:02:00.000 [main] INFO  com.test - Other message\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(parser_module, "MAX_GROUPS_IN_MEMORY", 1)

    counts, matched_logs = parse_log_directory(str(log_dir), ParseOptions())
    groups = list(matched_logs)
    same_message = next(group for group in groups if group["message"] == "Same message")

    assert counts == {"INFO": 3}
    assert len(groups) == 2
    assert same_message["count"] == 2
    assert same_message["line_numbers"] == [1, 2]
    assert matched_logs.diagnostics.files_scanned == 1
    assert matched_logs.diagnostics.parsed_entries == 3
    assert matched_logs.diagnostics.after_time == 3
    assert matched_logs.diagnostics.after_level == 3
    assert matched_logs.diagnostics.after_keyword == 3
    assert matched_logs.source_states["spill.log"]["stable"] is True


def test_matched_logs_store_diagnostics_are_independent_by_default():
    first = MatchedLogsStore("", 0)
    second = MatchedLogsStore("", 0)

    first.diagnostics.files_scanned = 1

    assert second.diagnostics.files_scanned == 0


def test_parse_log_directory_normalizes_naive_parse_options(temp_log_dir):
    counts, _logs = parse_log_directory(
        temp_log_dir,
        ParseOptions(start_time=datetime(2026, 6, 6, 10, 5)),
    )

    assert counts["ERROR"] == 1


def test_parse_log_directory_accepts_aware_parse_options(temp_log_dir):
    local_timezone = datetime.now().astimezone().tzinfo
    start_time = datetime(2026, 6, 6, 10, 5, tzinfo=local_timezone).astimezone(timezone.utc)

    counts, _logs = parse_log_directory(
        temp_log_dir,
        ParseOptions(start_time=start_time),
    )

    assert counts["ERROR"] == 1


def test_parse_entry_time_assigns_system_local_timezone():
    entry_time = parser_module._parse_entry_time("2026-06-06 10:05:00.123")

    assert entry_time.tzinfo == datetime.now().astimezone().tzinfo


def test_parse_logs_separates_multiline_message_and_exception(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "multiline.log"
    log_file.write_text(
        "2026-06-06 10:00:00.001 [main] INFO  com.test - Start request\n"
        "    payload=abc\n"
        "    requestId=123\n"
        "2026-06-06 10:10:00.555 [http-1] ERROR com.test - Fail\n"
        "java.lang.RuntimeException: Error\n"
        "    at com.test.App.main\n"
    )

    counts, errors = parse_logs(str(d), keyword="requestId=123")

    assert counts["INFO"] == 1
    assert counts["ERROR"] == 0
    assert len(errors) == 1
    assert errors[0]["message"] == "Start request"
    assert "payload=abc" in errors[0]["message_body"]
    assert "requestId=123" in errors[0]["message_body"]
    assert errors[0]["stacktrace"] == ""
    assert "full_text" not in errors[0]
    assert "stacktrace_lines" not in errors[0]
    assert "line_num" not in errors[0]


def test_iter_logs_yields_entries_in_parse_order(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "ordered.log"
    log_file.write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Info first\n"
        "2026-06-06 10:01:00.000 [main] ERROR com.test - Error second\n",
        encoding="utf-8",
    )

    entries = list(iter_logs(str(d)))

    assert [entry["message"] for entry in entries] == ["Info first", "Error second"]
    assert entries[0]["full_text"] == "Info first"
    assert entries[0]["stacktrace_lines"] == []


def test_iter_logs_keeps_in_range_entries_and_discards_filtered_continuations(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "range.log").write_text(
        "2026-06-06 09:59:00.000 [main] INFO  com.test - Before range\n"
        "    ignored continuation\n"
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Single line\n"
        "2026-06-06 10:01:00.000 [main] ERROR com.test - Failed\n"
        "java.lang.RuntimeException: Error\n"
        "    at com.test.App.main",
        encoding="utf-8",
    )

    entries = list(
        iter_logs(
            str(log_dir),
            start_time=datetime(2026, 6, 6, 10, 0),
            end_time=datetime(2026, 6, 6, 10, 1),
        )
    )

    assert [entry["message"] for entry in entries] == ["Single line", "Failed"]
    assert entries[0]["message_body"] == ""
    assert entries[1]["stacktrace_lines"] == [
        "    5: java.lang.RuntimeException: Error\n",
        "    6:     at com.test.App.main",
    ]
    assert "ignored continuation" not in entries[0]["full_text"]


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


def test_parse_logs_uses_external_sort_when_entries_exceed_threshold(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "external-sort.log"
    log_file.write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Info first\n"
        "2026-06-06 10:01:00.000 [main] ERROR com.test - Error second\n"
        "2026-06-06 10:02:00.000 [main] WARN  com.test - Warn third\n"
    )

    monkeypatch.setattr(parser_module, "MAX_SORT_GROUPS_IN_MEMORY", 1)

    _counts, errors = parse_logs(str(d), sort_by="level")

    assert [(entry["level"], entry["message"]) for entry in errors] == [
        ("ERROR", "Error second"),
        ("WARN", "Warn third"),
        ("INFO", "Info first"),
    ]


def test_parse_logs_spills_grouped_logs_and_merges_across_spills(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    log_file = d / "spilled.log"
    log_file.write_text(
        "2026-06-06 10:00:00.000 [main] INFO  com.test - Same message\n"
        "2026-06-06 10:01:00.000 [main] INFO  com.test - Same message\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(parser_module, "MAX_GROUPS_IN_MEMORY", 1)

    counts, errors = parse_logs(str(d))

    assert counts["INFO"] == 2
    assert len(errors) == 1
    assert errors[0]["count"] == 2
    assert errors[0]["line_numbers"] == [1, 2]


def test_spilled_group_preserves_occurrence_order(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for filename, timestamp, thread in (
        ("a.log", "10:00:00.001", "worker-a"),
        ("b.log", "10:01:00.002", "worker-b"),
        ("c.log", "10:02:00.003", "worker-c"),
    ):
        (log_dir / filename).write_text(
            f"2026-06-06 {timestamp} [{thread}] INFO  com.test - Shared message\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(parser_module, "MAX_GROUPS_IN_MEMORY", 1)

    _counts, groups = parse_logs(str(log_dir))

    assert len(groups) == 1
    assert groups[0]["count"] == len(groups[0]["occurrences"]) == 3
    assert groups[0]["occurrences"] == [
        {
            "filename": "a.log",
            "line_number": 1,
            "timestamp": "2026-06-06 10:00:00.001",
            "thread": "worker-a",
        },
        {
            "filename": "b.log",
            "line_number": 1,
            "timestamp": "2026-06-06 10:01:00.002",
            "thread": "worker-b",
        },
        {
            "filename": "c.log",
            "line_number": 1,
            "timestamp": "2026-06-06 10:02:00.003",
            "thread": "worker-c",
        },
    ]


def test_matched_log_pages_do_not_cache_all_groups(tmp_path):
    store_path = tmp_path / "logs.pkl"
    with open(store_path, "wb") as file_handle:
        for index in range(6):
            pickle.dump({"message": str(index)}, file_handle, protocol=pickle.HIGHEST_PROTOCOL)

    store = MatchedLogsStore(path=str(store_path), length=6)

    early_page = list(store.iter_page(1, 1))
    late_page = list(store.iter_page(4, 1))

    assert early_page == [{"message": "1"}]
    assert late_page == [{"message": "4"}]
    assert store._index_cache is None or store._index_cache == {}

    assert [log["message"] for log in store] == [str(index) for index in range(6)]
    assert store._index_cache is None or store._index_cache == {}

    assert store[5] == {"message": "5"}
    assert store._index_cache == {5: {"message": "5"}}


def test_matched_logs_store_caches_index_access(tmp_path, monkeypatch):
    store_path = tmp_path / "logs.pkl"
    with open(store_path, "wb") as file_handle:
        pickle.dump({"message": "first"}, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
        pickle.dump({"message": "second"}, file_handle, protocol=pickle.HIGHEST_PROTOCOL)

    store = parser_module.MatchedLogsStore(path=str(store_path), length=2)
    load_calls = []
    original_load = parser_module.pickle.load

    def counting_load(file_handle):
        load_calls.append(1)
        return original_load(file_handle)

    monkeypatch.setattr(parser_module.pickle, "load", counting_load)

    assert store[1]["message"] == "second"
    assert load_calls == [1, 1]
    assert store._index_cache == {1: {"message": "second"}}

    assert store[1]["message"] == "second"
    assert load_calls == [1, 1]


def test_source_changed_during_scan_is_unstable(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "changed.log"
    log_file.write_text(
        "2026-06-06 10:00:00.001 [main] INFO  com.test - Changed source\n",
        encoding="utf-8",
    )

    original_stat = parser_module.os.stat
    source_stat_calls = 0

    def changing_stat(path, *args, **kwargs):
        nonlocal source_stat_calls
        stat_result = original_stat(path, *args, **kwargs)
        if Path(path) == log_file:
            source_stat_calls += 1
            if source_stat_calls == 2:
                return SimpleNamespace(
                    st_size=stat_result.st_size,
                    st_mtime_ns=stat_result.st_mtime_ns + 1,
                )
        return stat_result

    monkeypatch.setattr(parser_module.os, "stat", changing_stat)

    _counts, logs = parse_log_directory(str(log_dir), ParseOptions())

    assert logs.source_states["changed.log"]["stable"] is False


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
