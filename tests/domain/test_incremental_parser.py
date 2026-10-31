from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import example, given, settings, strategies as st

from log_analyzer.domain.parser import iter_logs
from log_analyzer.domain.parsing.incremental import IncrementalEntryParser


LOG_PATTERN = (
    "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0} %X{traceId} - %msg%n"
)
SAMPLE_BYTES = (
    b"\xef\xbb\xbf"
    + (
        "2026-06-06 10:00:00.001 INFO  [main] com.test.App 排除-一 - 啟動 🚀\r\n"
        "    一般續行\r\n"
        "    malformed: "
    ).encode("utf-8")
    + b"\xff\r\n"
    + (
        "java.lang.RuntimeException: 失敗 😀\r\n"
        "    at com.test.App.main(App.java:1)\r\n"
        "2026-06-06 10:02:00.002 ERROR [worker-二] com.test.App 保留-二 - 連線失敗\n"
        "Caused by: java.net.ConnectException: 不可用\r\n"
        "    at com.test.Client.call(Client.java:2)\r\n"
        "2026-06-06 10:04:00.003 INFO  [main] com.test.App 排除-三 - 最後一筆沒有換行"
    ).encode("utf-8")
)


def _entry_view(entry):
    return {
        key: entry[key]
        for key in (
            "timestamp",
            "thread",
            "level",
            "logger",
            "message",
            "message_body",
            "full_text",
            "filename",
            "line_num",
            "stacktrace_lines",
            "mdc",
        )
        if key in entry
    }


def _known_split_points():
    encoded = SAMPLE_BYTES
    chinese_offset = encoded.index("排除".encode("utf-8"))
    crlf_offset = encoded.index(b"\r\n")
    return {
        1,
        2,
        chinese_offset + 1,
        chinese_offset + 2,
        crlf_offset + 1,
    }


@pytest.mark.parametrize(
    "time_range",
    [None, (datetime(2026, 6, 6, 10, 1), datetime(2026, 6, 6, 10, 3))],
)
@settings(max_examples=60, deadline=None)
@example(cut_points=_known_split_points())
@given(
    cut_points=st.sets(
        st.integers(min_value=1, max_value=len(SAMPLE_BYTES) - 1),
        max_size=48,
    )
)
def test_incremental_parser_matches_batch_entries_for_arbitrary_byte_splits(
    cut_points,
    time_range,
):
    with TemporaryDirectory() as directory:
        log_file = Path(directory) / "incremental.log"
        log_file.write_bytes(SAMPLE_BYTES)
        start_time, end_time = time_range or (None, None)

        parser = IncrementalEntryParser(
            filename=log_file.name,
            log_pattern=LOG_PATTERN,
            start_time=start_time,
            end_time=end_time,
        )
        boundaries = [0, *sorted(cut_points), len(SAMPLE_BYTES)]
        actual = []
        for start, end in zip(boundaries, boundaries[1:]):
            actual.extend(parser.feed(SAMPLE_BYTES[start:end]))
        actual.extend(parser.finish())

        expected = list(
            iter_logs(
                str(log_file),
                log_pattern=LOG_PATTERN,
                start_time=start_time,
                end_time=end_time,
            )
        )

        assert [_entry_view(entry) for entry in actual] == [
            _entry_view(entry) for entry in expected
        ]
        expected_raw_texts = [
            "2026-06-06 10:00:00.001 INFO  [main] com.test.App 排除-一 - 啟動 🚀\n"
            "    一般續行\n"
            "    malformed: �\n"
            "java.lang.RuntimeException: 失敗 😀\n"
            "    at com.test.App.main(App.java:1)\n",
            "2026-06-06 10:02:00.002 ERROR [worker-二] com.test.App 保留-二 - 連線失敗\n"
            "Caused by: java.net.ConnectException: 不可用\n"
            "    at com.test.Client.call(Client.java:2)\n",
            "2026-06-06 10:04:00.003 INFO  [main] com.test.App 排除-三 - 最後一筆沒有換行",
        ]
        if time_range is not None:
            expected_raw_texts = expected_raw_texts[1:2]
        assert [entry["raw_text"] for entry in actual] == expected_raw_texts

        if time_range is None:
            assert len(actual) == 3
            assert [entry["line_num"] for entry in actual] == [1, 6, 9]
            assert [entry["mdc"] for entry in actual] == [
                {"traceId": "排除-一"},
                {"traceId": "保留-二"},
                {"traceId": "排除-三"},
            ]
            assert actual[1]["stacktrace_lines"] == [
                "    7: Caused by: java.net.ConnectException: 不可用\n",
                "    8:     at com.test.Client.call(Client.java:2)\n",
            ]
        else:
            assert len(actual) == 1
            assert actual[0]["mdc"] == {"traceId": "保留-二"}
            assert actual[0]["line_num"] == 6
            assert actual[0]["stacktrace_lines"] == [
                "    7: Caused by: java.net.ConnectException: 不可用\n",
                "    8:     at com.test.Client.call(Client.java:2)\n",
            ]


def test_feed_keeps_current_entry_pending_until_explicit_finish():
    first_event = SAMPLE_BYTES[3 : SAMPLE_BYTES.index(b"2026-06-06 10:02")]
    parser = IncrementalEntryParser(filename="app.log", log_pattern=LOG_PATTERN)

    assert parser.feed(first_event) == []
    assert parser.line_number == 5
    pending = parser.pending
    assert pending is not None
    assert pending["message"] == "啟動 🚀"
    assert pending["raw_text"].endswith("    at com.test.App.main(App.java:1)\n")

    pending["stacktrace_lines"].append("mutated")
    pending["mdc"]["traceId"] = "mutated"
    assert parser.pending["stacktrace_lines"] == [
        "    4: java.lang.RuntimeException: 失敗 😀\n",
        "    5:     at com.test.App.main(App.java:1)\n",
    ]
    assert parser.pending["mdc"] == {"traceId": "排除-一"}

    finished = parser.finish()
    assert len(finished) == 1
    assert finished[0]["line_num"] == 1
    assert "malformed: �" in finished[0]["raw_text"]
    assert parser.pending is None
    assert parser.finish() == []
    with pytest.raises(RuntimeError):
        parser.feed(b"more bytes")


def test_parser_uses_universal_newlines_for_bare_cr_split_across_feeds():
    first_header = (
        "2026-06-06 10:00:00.001 INFO  [main] com.test.App trace-1 - first"
    ).encode("utf-8")
    second_header = (
        "2026-06-06 10:00:00.002 ERROR [main] com.test.App trace-2 - second\n"
    ).encode("utf-8")
    parser = IncrementalEntryParser(filename="app.log", log_pattern=LOG_PATTERN)

    assert parser.feed(first_header + b"\r") == []
    assert parser.line_number == 0
    assert parser.feed(b"continued\r") == []
    assert parser.line_number == 1
    completed = parser.feed(second_header)

    assert len(completed) == 1
    assert completed[0]["raw_text"] == (
        "2026-06-06 10:00:00.001 INFO  [main] com.test.App trace-1 - first\ncontinued\n"
    )
    assert completed[0]["line_num"] == 1
    assert parser.line_number == 3
    assert parser.finish()[0]["message"] == "second"
