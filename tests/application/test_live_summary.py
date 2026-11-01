import csv
import json
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

import log_analyzer.application.live.summary as summary_module
import log_analyzer.infrastructure.storage.live_summary as live_summary_module
from log_analyzer.application.analysis.service import (
    AnalysisRequest,
    ResultExportOptions,
    export_analysis_result,
)
from log_analyzer.application.live.session import TrackingSnapshot
from log_analyzer.application.live.summary import (
    TrackingAnalysisResult,
    build_tracking_summary,
)
from log_analyzer.domain.analysis_control import AnalysisCancelled, AnalysisControl
from log_analyzer.infrastructure.storage.live_events import LiveEventStore
from log_analyzer.infrastructure.storage.live_summary import LiveSummaryStore


def test_summary_stays_fixed_for_source_removal_appends_and_all_exporters(  # noqa: PLR0915 - end-to-end contract spans grouping and exporters
    tmp_path,
):
    source = tmp_path / "app.log"
    source.write_text("original source", encoding="utf-8")
    entries = [
        (
            _entry(
                "slow response",
                timestamp="2026-10-31 10:00:00",
                line_num=1,
                mdc={"request": "first"},
            ),
            0,
        ),
        (
            _entry(
                "database unavailable",
                level="ERROR",
                timestamp="2026-10-31 10:02:00",
                line_num=2,
            ),
            0,
        ),
        (
            _entry(
                "slow response",
                timestamp="2026-10-31 10:03:00",
                line_num=1,
                mdc={"request": "second"},
            ),
            1,
        ),
    ]
    event_store, snapshot = _create_snapshot(
        tmp_path,
        entries,
        path=source,
        completed=4,
        has_pending=True,
    )
    source.unlink()

    result = build_tracking_summary(snapshot)
    try:
        assert isinstance(result, TrackingAnalysisResult)
        assert result.tracking_snapshot is snapshot
        assert result.analysis_request is snapshot.conditions
        assert result.total_logs == 4
        assert result.matched_occurrences == snapshot.cutoff == 3
        assert result.matched_groups == 2
        assert result.counts == Counter({"INFO": 2, "ERROR": 1})
        assert result.level_summary == [("ERROR", 1), ("INFO", 2)]
        assert result.dashboard_summary is not None
        assert result.dashboard_summary.top_events[0].count == 2

        groups = list(result.matched_logs)
        assert groups == list(result.matched_logs)
        assert result.matched_logs[0] == groups[0]
        assert list(result.matched_logs.iter_page(1, 1)) == groups[1:]

        info_group = groups[0]
        assert info_group["message"] == "slow response"
        assert info_group["count"] == 2
        assert info_group["line_numbers"] == [1, 1]
        assert [item["sequence"] for item in info_group["occurrences"]] == [1, 3]
        assert [item["generation"] for item in info_group["occurrences"]] == [0, 1]
        assert [item["mdc"] for item in info_group["occurrences"]] == [
            {"request": "first"},
            {"request": "second"},
        ]
        assert [item["raw_text"] for item in info_group["occurrences"]] == [
            "INFO slow response\n",
            "INFO slow response\n",
        ]

        event_store.append(_entry("later event"), generation=2)
        assert result.matched_occurrences == 3
        assert [group["message"] for group in result.matched_logs] == [
            "slow response",
            "database unavailable",
        ]

        paths = {}
        for format_name in ("csv", "json", "md"):
            output_path = tmp_path / f"tracking.{format_name}"
            exported = export_analysis_result(
                result,
                ResultExportOptions(output_path=str(output_path), fmt=format_name),
            )
            assert len(exported) == 1
            paths[format_name] = output_path

        json_report = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert json_report["summary"] == {"ERROR": 1, "INFO": 2}
        assert sum(group["count"] for group in json_report["results"]) == 3
        json_occurrences = json_report["results"][0]["occurrences"]
        assert json_occurrences[1]["generation"] == 1
        assert json_occurrences[1]["raw_text"] == "INFO slow response\n"
        assert json_occurrences[1]["mdc"] == {"request": "second"}

        csv_rows = list(csv.reader(paths["csv"].open(encoding="utf-8", newline="")))
        assert ["ERROR", "1"] in csv_rows
        assert ["INFO", "2"] in csv_rows
        markdown = paths["md"].read_text(encoding="utf-8")
        assert "| ERROR | 1 |" in markdown
        assert "| INFO | 2 |" in markdown
    finally:
        result.matched_logs.release()
        event_store.close()


@pytest.mark.parametrize(
    ("sort_by", "expected_messages"),
    [
        ("time", ["early", "error", "warn", "late"]),
        ("level", ["error", "warn", "early", "late"]),
    ],
)
def test_group_order_matches_batch_time_and_level_sorting(
    tmp_path, sort_by, expected_messages
):
    entries = [
        (_entry("late", timestamp="2026-10-31 12:00:00"), 0),
        (_entry("error", level="ERROR", timestamp="2026-10-31 11:00:00"), 0),
        (_entry("warn", level="WARN", timestamp="2026-10-31 11:30:00"), 0),
        (_entry("early", timestamp="2026-10-31 10:00:00"), 0),
    ]
    event_store, snapshot = _create_snapshot(
        tmp_path,
        entries,
        sort_by=sort_by,
    )

    result = build_tracking_summary(snapshot)
    try:
        assert [group["message"] for group in result.matched_logs] == expected_messages
    finally:
        result.matched_logs.release()
        event_store.close()


def test_error_only_snapshot_does_not_recover_info_from_removed_source(tmp_path):
    source = tmp_path / "app.log"
    source.write_text("INFO hidden\nERROR retained\n", encoding="utf-8")
    event_store, snapshot = _create_snapshot(
        tmp_path,
        [
            (
                _entry(
                    "retained",
                    level="ERROR",
                    timestamp="2026-10-31 10:00:00",
                ),
                0,
            )
        ],
        path=source,
        levels=("ERROR",),
        completed=2,
        has_pending=True,
    )
    source.unlink()

    result = build_tracking_summary(snapshot)
    try:
        assert result.total_logs == 2
        assert result.matched_occurrences == 1
        assert result.counts == Counter({"ERROR": 1})
        assert [group["level"] for group in result.matched_logs] == ["ERROR"]
        assert all(group["message"] != "hidden" for group in result.matched_logs)
    finally:
        result.matched_logs.release()
        event_store.close()


def test_empty_snapshot_produces_an_empty_successful_summary(tmp_path):
    event_store, snapshot = _create_snapshot(tmp_path, [], completed=0)

    result = build_tracking_summary(snapshot)
    try:
        assert result.total_logs == 0
        assert result.matched_occurrences == 0
        assert result.matched_groups == 0
        assert result.counts == Counter()
        assert list(result.matched_logs) == []
        assert result.dashboard_summary is not None
        assert result.dashboard_summary.top_events == ()
    finally:
        result.matched_logs.release()
        event_store.close()


def test_cancellation_cleans_candidate_store_without_releasing_snapshot(
    tmp_path, monkeypatch
):
    event_store, snapshot = _create_snapshot(
        tmp_path,
        [(_entry("one"), 0), (_entry("two"), 0)],
    )
    candidate_directory = tmp_path / "summary-cancelled"
    candidate = LiveSummaryStore(candidate_directory)
    original_add = candidate.add_event
    control = AnalysisControl()

    def cancel_after_first_event(event):
        original_add(event)
        control.cancel()

    candidate.add_event = cancel_after_first_event
    monkeypatch.setattr(summary_module, "LiveSummaryStore", lambda: candidate)

    try:
        with pytest.raises(AnalysisCancelled):
            build_tracking_summary(snapshot, control=control)

        assert list(candidate_directory.glob("live-summary-*.sqlite3")) == []
        assert len(list(snapshot.events)) == 2
    finally:
        snapshot.release()
        event_store.close()


def test_storage_error_cleans_candidate_store_without_releasing_snapshot(
    tmp_path, monkeypatch
):
    event_store, snapshot = _create_snapshot(tmp_path, [(_entry("one"), 0)])
    candidate_directory = tmp_path / "summary-failed"
    candidate = LiveSummaryStore(candidate_directory)
    candidate._connection.execute(
        """
        CREATE TRIGGER reject_summary_occurrence
        BEFORE INSERT ON live_summary_occurrences
        BEGIN
            SELECT RAISE(ABORT, 'forced summary storage failure');
        END
        """
    )
    monkeypatch.setattr(summary_module, "LiveSummaryStore", lambda: candidate)

    try:
        with pytest.raises(OSError, match="無法寫入追蹤摘要事件") as error:
            build_tracking_summary(snapshot)

        assert isinstance(error.value.__cause__, sqlite3.IntegrityError)
        assert list(candidate_directory.glob("live-summary-*.sqlite3")) == []
        assert len(list(snapshot.events)) == 1
    finally:
        snapshot.release()
        event_store.close()


def test_releasing_successful_result_releases_its_store_and_snapshot(
    tmp_path, monkeypatch
):
    event_store, snapshot = _create_snapshot(tmp_path, [(_entry("saved"), 0)])
    event_directory = tmp_path / "source-events"
    summary_directory = tmp_path / "summary-result"
    candidate = LiveSummaryStore(summary_directory)
    monkeypatch.setattr(summary_module, "LiveSummaryStore", lambda: candidate)
    event_store.close()

    result = build_tracking_summary(snapshot)
    assert list(summary_directory.glob("live-summary-*.sqlite3"))
    assert list(event_directory.glob("live-events-*.sqlite3"))

    result.matched_logs.release()
    result.matched_logs.release()

    assert list(summary_directory.glob("live-summary-*.sqlite3")) == []
    assert list(event_directory.glob("live-events-*.sqlite3")) == []
    with pytest.raises(RuntimeError, match="released"):
        next(snapshot.events.iter_page(0, 1))


def test_summary_can_build_browse_export_and_release_across_threads(tmp_path):
    event_store, snapshot = _create_snapshot(
        tmp_path,
        [
            (_entry("first", line_num=1, mdc={"request": "one"}), 0),
            (_entry("second", line_num=2, mdc={"request": "two"}), 1),
        ],
    )

    result = None
    result_released = False
    with ThreadPoolExecutor(max_workers=1) as builder:
        try:
            result = builder.submit(build_tracking_summary, snapshot).result(
                timeout=10
            )
            # TUI reads and indexes the immutable summary on its main thread.
            first_group = result.matched_logs[0]
            assert first_group["count"] == 1
            assert list(result.matched_logs.iter_page(0, 2)) == list(
                result.matched_logs
            )

            def export_all_formats():
                exported = {}
                for format_name in ("csv", "json", "md"):
                    output_path = tmp_path / f"threaded.{format_name}"
                    exported[format_name] = export_analysis_result(
                        result,
                        ResultExportOptions(
                            output_path=str(output_path), fmt=format_name
                        ),
                    )
                return exported

            # Export workers use their own read connections after the UI has
            # already paged through the result.
            with ThreadPoolExecutor(max_workers=1) as exporter:
                exports = exporter.submit(export_all_formats).result(timeout=10)
            assert set(exports) == {"csv", "json", "md"}
            assert all(len(paths) == 1 for paths in exports.values())
            assert sum(
                group["count"] for group in json.loads(
                    (tmp_path / "threaded.json").read_text(encoding="utf-8")
                )["results"]
            ) == 2

            # Release is owned by the UI/main thread, not the build worker.
            result.matched_logs.release()
            result.matched_logs.release()
            result_released = True
        finally:
            try:
                if result is None:
                    snapshot.release()
                elif not result_released:
                    # If an assertion fails, dispose the build connection on
                    # its owner and release the source snapshot on the UI thread.
                    builder.submit(result.matched_logs._store.release).result(
                        timeout=10
                    )
                    snapshot.release()
            finally:
                event_store.close()


def test_summary_page_closes_read_connection_when_generator_is_closed(
    tmp_path, monkeypatch
):
    store = LiveSummaryStore(tmp_path)
    store.add_event(_summary_event(1))
    store.finalize("time")
    read_connections = _track_read_connections(monkeypatch)
    page = store.iter_page(0, 1)

    try:
        assert next(page)["message"] == "message-1"
        assert len(read_connections) == 1
        page.close()
        _assert_connection_closed(read_connections[0])
    finally:
        page.close()
        store.release()


def test_summary_page_closes_read_connection_after_group_read_failure(
    tmp_path, monkeypatch
):
    store = LiveSummaryStore(tmp_path)
    store.add_event(_summary_event(1))
    store.finalize("time")
    read_connections = _track_read_connections(monkeypatch)

    def fail_group_read(*_args):
        raise OSError("injected group read failure")

    monkeypatch.setattr(store, "_read_group", fail_group_read)
    page = store.iter_page(0, 1)
    try:
        with pytest.raises(OSError, match="injected group read failure"):
            next(page)

        assert len(read_connections) == 1
        _assert_connection_closed(read_connections[0])
    finally:
        page.close()
        store.release()


def _track_read_connections(monkeypatch):
    original_connect = sqlite3.connect
    read_connections = []

    def track_connect(database, *args, **kwargs):
        connection = original_connect(database, *args, **kwargs)
        if kwargs.get("uri") and "mode=ro" in str(database):
            read_connections.append(connection)
        return connection

    monkeypatch.setattr(live_summary_module.sqlite3, "connect", track_connect)
    return read_connections


def _assert_connection_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def _summary_event(sequence):
    return {
        "level": "INFO",
        "logger": "example.Worker",
        "message": f"message-{sequence}",
        "message_body": "",
        "stacktrace_lines": [],
        "timestamp": "2026-10-31 10:00:00",
        "sequence": sequence,
        "generation": 0,
        "thread": "main",
        "filename": "app.log",
        "line_num": sequence,
        "mdc": {},
        "raw_text": f"INFO message-{sequence}\n",
    }


def _create_snapshot(  # noqa: PLR0913 - fixture mirrors fixed snapshot metadata
    tmp_path,
    events,
    *,
    path=None,
    sort_by="time",
    levels=("ERROR", "WARN", "INFO"),
    completed=None,
    has_pending=False,
):
    source_path = tmp_path / "app.log" if path is None else path
    conditions = AnalysisRequest(
        path=str(source_path),
        sort_by=sort_by,
        levels=levels,
    )
    event_store = LiveEventStore(directory=tmp_path / "source-events")
    for entry, generation in events:
        event_store.append(entry, generation)
    event_snapshot = event_store.snapshot()
    snapshot = TrackingSnapshot(
        session_id="session-test",
        conditions=conditions,
        captured_at=datetime.now(timezone.utc),
        events=event_snapshot,
        completed=len(events) if completed is None else completed,
        received_bytes=0,
        has_pending=has_pending,
        gaps=(),
    )
    return event_store, snapshot


def _entry(  # noqa: PLR0913 - fixture exposes fields used by grouping contracts
    message,
    *,
    level="INFO",
    timestamp="2026-10-31 10:00:00",
    line_num=1,
    filename="app.log",
    thread="main",
    logger="example.Worker",
    message_body="",
    stacktrace_lines=(),
    mdc=None,
):
    raw_text = f"{level} {message}\n"
    return {
        "timestamp": timestamp,
        "thread": thread,
        "level": level,
        "logger": logger,
        "message": message,
        "message_body": message_body,
        "full_text": raw_text,
        "filename": filename,
        "line_num": line_num,
        "stacktrace_lines": list(stacktrace_lines),
        "mdc": dict(mdc or {}),
        "raw_text": raw_text,
    }
