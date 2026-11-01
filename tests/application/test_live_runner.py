import asyncio
from dataclasses import replace

import pytest

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.runner import LiveRunner
from log_analyzer.application.live.session import LiveSession
import log_analyzer.application.live.runner as runner_module


def log(message, level="ERROR"):
    return f"2026-10-31 12:00:00.000 [main] {level} com.test - {message}\n".encode()


async def wait_for_condition(changed, predicate, description, timeout=6):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if predicate():
            return
        changed.clear()
        if predicate():
            continue
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {description}")
        try:
            await asyncio.wait_for(changed.wait(), timeout=remaining)
        except TimeoutError as error:
            raise AssertionError(f"timed out waiting for {description}") from error


def test_runner_reads_existing_bytes_then_appended_events_and_stops_once(tmp_path):
    source = tmp_path / "app.log"
    initial = log("first") + log("pending")
    source.write_bytes(initial)
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    changed = asyncio.Event()
    batches = []
    callback_order = []

    def on_events(events):
        batches.append(events)
        callback_order.append(("events", tuple(event["message"] for event in events)))
        changed.set()

    def on_update():
        callback_order.append(("update", session.offset))
        changed.set()

    runner = LiveRunner(session, on_events=on_events, on_update=on_update)

    async def run():
        try:
            await runner.start()
            first_task = runner._drive_task
            await runner.start()
            assert runner._drive_task is first_task
            with pytest.raises(ValueError, match="條件已變更"):
                await runner.start(replace(session.conditions, keyword="changed"))
            assert runner._drive_task is first_task

            await wait_for_condition(
                changed,
                lambda: session.offset == len(initial) and batches,
                "initial bytes consumed and first event delivered",
            )
            assert [event["message"] for event in batches[0]] == ["first"]
            assert session.pending["message"] == "pending"

            with source.open("ab") as stream:
                stream.write(log("next"))
            expected_offset = len(initial) + len(log("next"))
            await wait_for_condition(
                changed,
                lambda: session.offset == expected_offset and len(batches) >= 2,
                "appended bytes consumed and pending event completed",
            )
            assert [event["message"] for event in batches[1]] == ["pending"]
            assert "Exception" not in batches[1][0]["raw_text"]

            final_events = await runner.stop()
            assert [event["message"] for event in final_events] == ["next"]
            assert [event["message"] for batch in batches for event in batch] == [
                "first",
                "pending",
                "next",
            ]
            assert [event["message"] for event in await runner.stop()] == []
            assert len(batches) == 3
            assert callback_order.index(("events", ("first",))) < next(
                index
                for index, item in enumerate(callback_order)
                if item[0] == "update"
            )
        finally:
            if runner.running:
                await runner.pause()
            session.close()

    asyncio.run(run())


def test_pause_stops_reads_and_resume_catches_up_with_pending_stack(tmp_path):
    source = tmp_path / "app.log"
    initial = log("first") + log("pending")
    source.write_bytes(initial)
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    changed = asyncio.Event()
    delivered = []
    step_calls = 0
    original_step = session.step

    def counted_step(max_bytes=65536):
        nonlocal step_calls
        step_calls += 1
        return original_step(max_bytes)

    session.step = counted_step
    runner = LiveRunner(
        session,
        on_events=lambda events: (delivered.extend(events), changed.set()),
        on_update=changed.set,
    )

    async def run():
        try:
            await runner.start()
            await wait_for_condition(
                changed,
                lambda: session.offset == len(initial) and delivered,
                "initial pending event established",
            )
            await runner.pause()
            paused_offset = session.offset
            paused_steps = step_calls
            assert not runner.running
            assert session.pending["message"] == "pending"

            with source.open("ab") as stream:
                stream.write(b"java.lang.Exception: retained stack\n" + log("next"))
            assert session.offset == paused_offset
            assert step_calls == paused_steps

            await runner.start()
            quick_task = runner._drive_task
            await runner.pause()
            assert quick_task.done()
            assert session.offset == paused_offset
            assert step_calls == paused_steps

            await runner.start()
            await wait_for_condition(
                changed,
                lambda: (
                    session.offset == source.stat().st_size
                    and any(event["message"] == "pending" for event in delivered)
                ),
                "resume catches up and completes the pending event",
            )
            pending = next(
                event for event in delivered if event["message"] == "pending"
            )
            assert "Exception: retained stack" in pending["raw_text"]
            assert session.pending["message"] == "next"
            assert [event["message"] for event in await runner.stop()] == ["next"]
        finally:
            if runner.running:
                await runner.pause()
            session.close()

    asyncio.run(run())


def test_pause_closes_watcher_and_stop_does_not_close_session(tmp_path, monkeypatch):
    source = tmp_path / "app.log"
    source.write_bytes(log("pending"))
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    watcher_started = asyncio.Event()
    watcher_closed = asyncio.Event()

    async def blocking_watcher(_path, stop_event):
        watcher_started.set()
        try:
            await stop_event.wait()
        finally:
            watcher_closed.set()
        if False:
            yield None

    monkeypatch.setattr(runner_module, "watch_source", blocking_watcher)
    runner = LiveRunner(session)

    async def run():
        try:
            await runner.start()
            await asyncio.wait_for(watcher_started.wait(), timeout=2)
            await runner.pause()
            assert watcher_closed.is_set()
            assert runner.running is False
            assert session._closed is False

            await runner.stop()
            assert session._closed is False
        finally:
            session.close()

    asyncio.run(run())


def test_deleted_source_recovers_and_rotations_keep_generation_order(tmp_path):
    source = tmp_path / "app.log"
    rotated = tmp_path / "app.log.1"
    initial = log("before-delete") + log("old-tail")
    source.write_bytes(initial)
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    changed = asyncio.Event()
    delivered = []
    runner = LiveRunner(
        session,
        on_events=lambda events: (delivered.extend(events), changed.set()),
        on_update=changed.set,
    )

    async def run():
        try:
            await runner.start()
            await wait_for_condition(
                changed,
                lambda: session.offset == len(initial) and delivered,
                "initial source consumed",
            )
            source.unlink()
            await wait_for_condition(
                changed,
                lambda: session.state == "interrupted",
                "deleted source reported interrupted",
            )

            restored = log("restored") + log("restored-tail")
            source.write_bytes(restored)
            await wait_for_condition(
                changed,
                lambda: (
                    session.offset == len(restored)
                    and any(event["message"] == "restored" for event in delivered)
                ),
                "restored source consumed in a new generation",
            )
            assert any(
                event["message"] == "old-tail" and event["generation"] == 0
                for event in delivered
            )
            assert any(
                event["message"] == "restored" and event["generation"] == 1
                for event in delivered
            )

            rotated_bytes = log("rotated") + log("rotated-tail")
            source.replace(rotated)
            source.write_bytes(rotated_bytes)
            await wait_for_condition(
                changed,
                lambda: (
                    session.offset == len(rotated_bytes)
                    and any(event["message"] == "rotated" for event in delivered)
                ),
                "rotated source consumed in a separate generation",
            )
            assert any(
                event["message"] == "restored-tail" and event["generation"] == 1
                for event in delivered
            )
            assert any(
                event["message"] == "rotated" and event["generation"] == 2
                for event in delivered
            )
            final_events = await runner.stop()
            assert [
                (event["message"], event["generation"]) for event in final_events
            ] == [("rotated-tail", 2)]
        finally:
            if runner.running:
                await runner.pause()
            session.close()

    asyncio.run(run())


def test_step_error_delivers_saved_prefix_and_remains_observable(tmp_path, monkeypatch):
    source = tmp_path / "app.log"
    source.write_bytes(log("saved") + log("fails") + log("pending"))
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    changed = asyncio.Event()
    delivered = []
    callback_order = []
    original_append = session._store.append
    append_calls = 0

    def fail_second_append(entry, generation):
        nonlocal append_calls
        append_calls += 1
        if append_calls == 2:
            raise OSError("store write failed")
        return original_append(entry, generation)

    monkeypatch.setattr(session._store, "append", fail_second_append)
    runner = None

    def on_events(events):
        delivered.extend(events)
        callback_order.append(("events", tuple(event["message"] for event in events)))
        changed.set()

    def on_update():
        callback_order.append(("update", runner.failure))
        changed.set()

    runner = LiveRunner(session, on_events=on_events, on_update=on_update)

    async def run():
        try:
            await runner.start()
            await wait_for_condition(
                changed,
                lambda: runner.failure is not None,
                "runner failure reported",
            )
            assert isinstance(runner.failure, OSError)
            assert str(runner.failure) == "store write failed"
            assert session.state == "error"
            assert session.error == "store write failed"
            assert session.matched == 1
            assert [event["message"] for event in delivered] == ["saved"]
            assert callback_order[0] == ("events", ("saved",))
            assert callback_order[1][0] == "update"
            assert isinstance(callback_order[1][1], OSError)
            await runner.pause()
            assert runner.running is False
        finally:
            if runner.running:
                await runner.pause()
            session.close()

    asyncio.run(run())


def test_pause_finishes_handoff_when_caller_is_cancelled_during_watcher_cleanup(
    tmp_path, monkeypatch
):
    source = tmp_path / "app.log"
    source.write_bytes(log("pending"))
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    watcher_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def blocking_watcher(_path, stop_event):
        watcher_started.set()
        try:
            await stop_event.wait()
            yield None
        finally:
            cleanup_started.set()
            await allow_cleanup.wait()

    monkeypatch.setattr(runner_module, "watch_source", blocking_watcher)
    updates = []
    runner = LiveRunner(session, on_update=lambda: updates.append(session.state))

    async def run():
        pause_task = None
        try:
            await runner.start()
            await asyncio.wait_for(watcher_started.wait(), timeout=2)
            pause_task = asyncio.create_task(runner.pause())
            await asyncio.wait_for(cleanup_started.wait(), timeout=2)
            pause_task.cancel()

            checkpoint = asyncio.get_running_loop().create_future()
            asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
            await asyncio.wait_for(checkpoint, timeout=2)
            assert not pause_task.done()

            allow_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await pause_task
            assert cleanup_started.is_set()
            assert session.state == "paused"
            assert runner.running is False
            assert updates[-1] == "paused"
        finally:
            allow_cleanup.set()
            if pause_task is not None and not pause_task.done():
                pause_task.cancel()
                try:
                    await pause_task
                except asyncio.CancelledError:
                    pass
            if runner.running:
                await runner.pause()
            session.close()

    asyncio.run(run())


@pytest.mark.parametrize("callback_fails", [False, True])
def test_stop_end_failure_delivers_saved_prefix_and_records_failure(
    tmp_path, monkeypatch, callback_fails
):
    source = tmp_path / "app.log"
    source.write_bytes(log("pending"))
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    changed = asyncio.Event()
    delivered = []
    callback_order = []
    runner = None
    callback_error = RuntimeError("event callback failed")

    def on_events(events):
        delivered.extend(events)
        callback_order.append(("events", tuple(event["message"] for event in events)))
        if callback_fails:
            raise callback_error

    def on_update():
        callback_order.append(("update", runner.failure))
        changed.set()

    runner = LiveRunner(session, on_events=on_events, on_update=on_update)
    original_append = session._store.append
    append_calls = 0

    def fail_second_append(entry, generation):
        nonlocal append_calls
        append_calls += 1
        if append_calls == 2:
            raise OSError("store write failed during end")
        return original_append(entry, generation)

    async def run():
        try:
            await runner.start()
            await wait_for_condition(
                changed,
                lambda: session.offset == len(log("pending")),
                "initial bytes consumed before end",
            )
            callback_order.clear()

            end_parser = type(session._parser)(source.name)
            end_entries = end_parser.feed(log("saved") + log("fails") + log("tail"))
            assert [entry["message"] for entry in end_entries] == ["saved", "fails"]
            monkeypatch.setattr(session._parser, "finish", lambda: end_entries)
            monkeypatch.setattr(session._store, "append", fail_second_append)

            with pytest.raises(OSError, match="store write failed during end") as error:
                await runner.stop()

            assert runner.failure is error.value
            assert str(runner.failure) == "store write failed during end"
            assert (error.value.__cause__ is callback_error) is callback_fails
            assert (session.error, session.state, session.matched) == (
                "store write failed during end",
                "error",
                1,
            )
            assert [event["message"] for event in delivered] == ["saved"]
            assert callback_order == [
                ("events", ("saved",)),
                ("update", error.value),
            ]
            assert runner.running is False
        finally:
            if runner.running:
                await runner.pause()
            session.close()

    asyncio.run(run())


def test_drive_failure_notifies_again_after_background_task_finishes(
    tmp_path, monkeypatch
):
    source = tmp_path / "app.log"
    source.write_bytes(log("pending"))
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    watcher_started = asyncio.Event()
    trigger_read = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    changed = asyncio.Event()
    updates = []
    runner = None

    async def controlled_watcher(_path, _stop_event):
        watcher_started.set()
        try:
            await trigger_read.wait()
            yield None
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await allow_cleanup.wait()

    def on_update():
        updates.append((session.state, runner.running))
        changed.set()

    monkeypatch.setattr(runner_module, "watch_source", controlled_watcher)
    monkeypatch.setattr(
        session._store,
        "append",
        lambda _entry, _generation: (_ for _ in ()).throw(OSError("append failed")),
    )
    runner = LiveRunner(session, on_update=on_update)

    async def run():
        try:
            await runner.start()
            await asyncio.wait_for(watcher_started.wait(), timeout=2)
            with source.open("ab") as stream:
                stream.write(log("next"))
            trigger_read.set()
            await asyncio.wait_for(cleanup_started.wait(), timeout=2)

            assert runner.failure is not None
            assert any(state == "error" and running for state, running in updates)
            allow_cleanup.set()
            await wait_for_condition(
                changed,
                lambda: any(
                    state == "error" and not running for state, running in updates
                ),
                "terminal error update after runner task completion",
            )
            assert runner.running is False
        finally:
            allow_cleanup.set()
            if runner.running:
                await runner.pause()
            session.close()

    asyncio.run(run())


def test_runner_rejects_use_from_a_different_event_loop(tmp_path):
    source = tmp_path / "app.log"
    source.write_bytes(b"")
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    runner = LiveRunner(session)

    async def start_and_pause():
        await runner.start()
        await runner.pause()

    asyncio.run(start_and_pause())
    try:
        with pytest.raises(RuntimeError, match="event loop"):
            asyncio.run(runner.pause())
    finally:
        session.close()
