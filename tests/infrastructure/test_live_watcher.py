import asyncio
from contextlib import aclosing

import pytest
from watchfiles import Change

import log_analyzer.infrastructure.sources.watcher as watcher_module
from log_analyzer.infrastructure.sources.watcher import watch_source


def test_watcher_filters_to_target_and_yields_only_hints(monkeypatch, tmp_path):
    source = tmp_path / "app.log"
    sibling = tmp_path / "other.log"
    source.write_bytes(b"")
    stop_event = asyncio.Event()
    captured = {}

    async def fake_awatch(*paths, **options):
        captured["paths"] = paths
        captured.update(options)
        watch_filter = options["watch_filter"]
        assert watch_filter(Change.added, str(source))
        assert not watch_filter(Change.modified, str(sibling))
        yield {(Change.added, str(source))}
        yield set()

    monkeypatch.setattr(watcher_module, "awatch", fake_awatch)

    async def collect_hints():
        async with aclosing(watch_source(source, stop_event)) as watcher:
            return [await anext(watcher), await anext(watcher)]

    assert asyncio.run(collect_hints()) == [None, None]
    assert captured["paths"] == (str(tmp_path.resolve()),)
    assert captured["recursive"] is False
    assert captured["rust_timeout"] == 500
    assert captured["yield_on_timeout"] is True
    assert captured["stop_event"] is stop_event


def test_watcher_stops_when_stop_event_is_set(tmp_path):
    source = tmp_path / "app.log"
    source.write_bytes(b"")

    async def stop_watching():
        stop_event = asyncio.Event()
        watcher = watch_source(source, stop_event)
        try:
            first_hint = asyncio.create_task(anext(watcher))
            assert await asyncio.wait_for(first_hint, timeout=3) is None

            next_hint = asyncio.create_task(anext(watcher))
            stop_event.set()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(next_hint, timeout=3)
        finally:
            await watcher.aclose()

    asyncio.run(stop_watching())
