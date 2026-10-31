import json
import sqlite3

import pytest

from log_analyzer.infrastructure.storage.live_events import (
    LiveEventStore,
    LiveStorageLimitError,
)


def test_append_keeps_identical_events_as_distinct_sequenced_occurrences(tmp_path):
    store = LiveEventStore(tmp_path)
    event = {"raw_text": "same", "mdc": {"request": "r-1"}}
    try:
        assert store.append(event, generation=3) == 1
        assert store.append(event, generation=3) == 2

        stored = list(store.iter_page(0, 10))

        assert stored == [
            {
                "raw_text": "same",
                "mdc": {"request": "r-1"},
                "sequence": 1,
                "generation": 3,
            },
            {
                "raw_text": "same",
                "mdc": {"request": "r-1"},
                "sequence": 2,
                "generation": 3,
            },
        ]
    finally:
        store.close()


def test_store_pages_use_zero_based_start_and_respect_a_cutoff(tmp_path):
    store = LiveEventStore(tmp_path)
    try:
        for number in range(1, 5):
            store.append({"raw_text": f"event-{number}"}, generation=0)

        page = list(store.iter_page(start=1, size=2))
        before_cutoff = list(store.iter_page(start=0, size=4, cutoff=2))
        past_cutoff = list(store.iter_page(start=2, size=4, cutoff=2))

        assert [event["sequence"] for event in page] == [2, 3]
        assert [event["sequence"] for event in before_cutoff] == [1, 2]
        assert past_cutoff == []
    finally:
        store.close()


def test_snapshot_keeps_a_fixed_cutoff_and_survives_store_close(tmp_path):
    store = LiveEventStore(tmp_path)
    store.append({"raw_text": "captured"}, generation=1)
    snapshot = store.snapshot()
    store.append({"raw_text": "later"}, generation=1)

    store.close()
    try:
        assert snapshot.cutoff == 1
        assert len(snapshot) == 1
        assert list(snapshot) == [
            {
                "raw_text": "captured",
                "sequence": 1,
                "generation": 1,
            }
        ]
        assert list(snapshot.iter_page(start=0, size=5)) == list(snapshot)
    finally:
        snapshot.release()


def test_append_and_returned_pages_are_independent_of_mutable_inputs(tmp_path):
    store = LiveEventStore(tmp_path)
    event = {"raw_text": "before", "mdc": {"key": "before"}}
    try:
        store.append(event, generation=2)
        event["raw_text"] = "after"
        event["mdc"]["key"] = "after"

        first_read = next(store.iter_page(start=0, size=1))
        first_read["mdc"]["key"] = "changed result"
        second_read = next(store.iter_page(start=0, size=1))

        assert second_read == {
            "raw_text": "before",
            "mdc": {"key": "before"},
            "sequence": 1,
            "generation": 2,
        }
    finally:
        store.close()


def test_json_payload_round_trip_preserves_lone_surrogates(tmp_path):
    store = LiveEventStore(tmp_path)
    raw_text = "prefix-\ud800-suffix"
    try:
        store.append({"raw_text": raw_text}, generation=0)

        event = next(store.iter_page(start=0, size=1))

        assert event["raw_text"] == raw_text
    finally:
        store.close()


def test_quota_failure_preserves_existing_entries_and_count(tmp_path):
    first_event = {"raw_text": "accepted"}
    payload_bytes = len(
        json.dumps(first_event, ensure_ascii=True, separators=(",", ":")).encode(
            "ascii"
        )
    )
    store = LiveEventStore(tmp_path, max_bytes=payload_bytes)
    try:
        store.append(first_event, generation=0)

        with pytest.raises(LiveStorageLimitError) as error:
            store.append({"raw_text": "rejected"}, generation=0)

        assert isinstance(error.value, OSError)
        assert len(store) == 1
        assert [event["sequence"] for event in store.iter_page(0, 5)] == [1]
    finally:
        store.close()


def test_sqlite_transaction_failure_rolls_back_and_does_not_consume_sequence(
    tmp_path,
):
    store = LiveEventStore(tmp_path)
    try:
        store._connection.execute(
            """
            CREATE TRIGGER reject_live_event
            BEFORE INSERT ON live_events
            BEGIN
                SELECT RAISE(ABORT, 'forced insert failure');
            END
            """
        )

        with pytest.raises(OSError, match="無法寫入即時事件") as error:
            store.append({"raw_text": "not stored"}, generation=0)

        assert isinstance(error.value.__cause__, sqlite3.IntegrityError)
        assert len(store) == 0
        store._connection.execute("DROP TRIGGER reject_live_event")
        assert store.append({"raw_text": "stored"}, generation=0) == 1
    finally:
        store.close()


def test_sqlite_read_failure_is_exposed_as_oserror_with_original_cause(tmp_path):
    store = LiveEventStore(tmp_path)
    try:
        store.append({"raw_text": "stored"}, generation=0)
        store._connection.execute("DROP TABLE live_events")

        with pytest.raises(OSError, match="無法讀取即時事件") as error:
            list(store.iter_page(start=0, size=1))

        assert isinstance(error.value.__cause__, sqlite3.Error)
    finally:
        store.close()


def test_owned_temporary_directory_is_removed_when_store_closes():
    store = LiveEventStore()
    directory = store._directory
    first_snapshot = store.snapshot()
    second_snapshot = store.snapshot()
    assert directory.exists()

    store.close()
    store.close()
    assert directory.exists()

    first_snapshot.release()
    assert directory.exists()

    second_snapshot.release()

    assert not directory.exists()


def test_caller_directory_and_existing_files_survive_until_snapshot_release(
    tmp_path,
):
    source = tmp_path / "source.log"
    source.write_text("source data", encoding="utf-8")
    store = LiveEventStore(tmp_path)
    store.append({"raw_text": "event"}, generation=0)
    snapshot = store.snapshot()
    database_files = list(tmp_path.glob("live-events-*.sqlite3"))
    assert len(database_files) == 1

    store.close()
    try:
        assert database_files[0].exists()
        assert list(snapshot)[0]["raw_text"] == "event"
    finally:
        snapshot.release()
        snapshot.release()

    assert source.read_text(encoding="utf-8") == "source data"
    assert tmp_path.exists()
    assert not database_files[0].exists()


def test_snapshot_pages_captured_text_after_original_file_is_removed(tmp_path):
    source = tmp_path / "source.log"
    source.write_text("original line", encoding="utf-8")
    storage_directory = tmp_path / "live-store"
    store = LiveEventStore(storage_directory)
    store.append(
        {"source_path": str(source), "raw_text": source.read_text(encoding="utf-8")},
        generation=4,
    )
    snapshot = store.snapshot()
    source.unlink()

    store.close()
    try:
        event = next(snapshot.iter_page(start=0, size=1))

        assert event["raw_text"] == "original line"
        assert not source.exists()
    finally:
        snapshot.release()
