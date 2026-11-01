"""以合成日誌比較批次重掃、即時事件串流與按需追蹤摘要。

此工具不讀取使用者日誌，也不匯出正式結果。預設規模適合本機試跑；
可用 --entries 與 --batch-repeats 明確擴大工作量。
"""

from __future__ import annotations

import argparse
import asyncio
import builtins
import json
import math
import platform
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import OptionList

from log_analyzer.application.analysis.service import AnalysisRequest, analyze_logs
from log_analyzer.application.live import summary as live_summary_module
from log_analyzer.application.live.runner import LiveRunner
from log_analyzer.application.live.session import LiveSession
from log_analyzer.domain.parsing import scanner as scanner_module
from log_analyzer.infrastructure.sources import incremental as incremental_module
from log_analyzer.presentation.tui.components.live_panel import LivePanel

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_LIVE_CSS = (
    PROJECT_ROOT / "src" / "log_analyzer" / "tcss" / "tui.live.tcss"
)
_KEYWORD = "BENCHMARK"
_LEVELS = ("ERROR", "WARN", "INFO")


def _timestamp(hour: int, index: int) -> str:
    """將毫秒索引轉成可跨分鐘與日期遞增的日誌時間。"""
    value = datetime(2026, 10, 31, hour) + timedelta(milliseconds=index)
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _event_line(index: int) -> bytes:
    timestamp = _timestamp(12, index)
    if index % 2 == 0:
        thread = "bench-repeated"
        level = "ERROR"
        message = f"{_KEYWORD} repeated failure"
    else:
        thread = f"worker-{index % 16:02d}"
        level = _LEVELS[index % len(_LEVELS)]
        message = f"{_KEYWORD} unique failure {index:08d}"
    return (
        f"{timestamp} [{thread}] {level} example.Benchmark - {message}\n"
    ).encode("utf-8")


def _boundary_line(index: int) -> bytes:
    timestamp = _timestamp(13, index)
    return (
        f"{timestamp} [bench-boundary] INFO example.Benchmark - BOUNDARY marker\n"
    ).encode("utf-8")


def _build_chunks(entries: int, chunk_entries: int) -> list[bytes]:
    """建立固定 UTF-8、LF 合成輸入並在每批加入篩選掉的事件邊界。"""
    chunks: list[bytes] = []
    for start in range(0, entries, chunk_entries):
        end = min(start + chunk_entries, entries)
        chunk = bytearray()
        for index in range(start, end):
            chunk.extend(_event_line(index))
        chunks.append(bytes(chunk))
    chunks.append(_boundary_line(entries))
    return chunks


def _tree_bytes(root: Path) -> int:
    return sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file()
    )


class _TemporaryBytesSampler:
    """以實際檔案 stat 取樣隔離暫存目錄的位元組數。"""

    def __init__(self, root: Path, interval: float = 0.01) -> None:
        self.root = root
        self.interval = interval
        self.peak_observed = 0
        self.current_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def sample(self) -> None:
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        self.current_bytes = total
        self.peak_observed = max(self.peak_observed, total)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.sample()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.sample()


def _write_chunks(
    path: Path,
    chunks: list[bytes],
    interval_ms: float,
    line_write_starts: dict[int, float] | None = None,
) -> dict[str, Any]:
    """依絕對時間表追加資料，回傳實際寫入量與排程觀測。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    interval = interval_ms / 1000
    schedule_start = time.perf_counter()
    append_times: list[float] = []
    line_number = 1
    written_bytes = 0
    with path.open("ab") as handle:
        for index, chunk in enumerate(chunks):
            due = schedule_start + index * interval
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            append_started = time.perf_counter()
            append_times.append(append_started)
            line_count = chunk.count(b"\n")
            if line_write_starts is not None:
                for current_line in range(line_number, line_number + line_count):
                    line_write_starts[current_line] = append_started
            handle.write(chunk)
            handle.flush()
            written_bytes += len(chunk)
            line_number += line_count
    elapsed = max(time.perf_counter() - schedule_start, 1e-9)
    intervals = [
        later - earlier
        for earlier, later in zip(append_times, append_times[1:])
    ]
    return {
        "bytes_written": written_bytes,
        "elapsed_seconds": elapsed,
        "observed_bytes_per_second": written_bytes / elapsed,
        "scheduled_interval_ms": interval_ms,
        "observed_intervals_ms": _latency_stats(intervals),
        "chunks": len(chunks),
    }


def _latency_stats(values_seconds: list[float]) -> dict[str, float | int]:
    values_ms = sorted(value * 1000 for value in values_seconds)
    if not values_ms:
        return {"count": 0, "median_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    p95_index = max(0, math.ceil(len(values_ms) * 0.95) - 1)
    return {
        "count": len(values_ms),
        "median_ms": statistics.median(values_ms),
        "p95_ms": values_ms[p95_index],
        "max_ms": values_ms[-1],
    }


def _measure_batch_once(
    source: Path,
    expected_entries: int,
    expected_groups: int,
    temp_root: Path,
    append_started: float | None = None,
) -> dict[str, Any]:
    temp_root.mkdir(parents=True, exist_ok=True)
    bytes_consumed = 0
    original_scanner = scanner_module.iter_scanned_lines
    previous_tempdir = tempfile.tempdir
    result = None

    def counted_lines(*args, **kwargs):
        nonlocal bytes_consumed
        for line_number, line, match in original_scanner(*args, **kwargs):
            # 合成來源固定為無 BOM 的 UTF-8/LF；surrogateescape 可逆映射原始 bytes。
            bytes_consumed += len(line.encode("utf-8", errors="surrogateescape"))
            yield line_number, line, match

    scanner_module.iter_scanned_lines = counted_lines
    tempfile.tempdir = str(temp_root)
    sampler = _TemporaryBytesSampler(temp_root)
    sampler.start()
    tracemalloc.start()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        result = analyze_logs(
            AnalysisRequest(str(source), keyword=_KEYWORD, levels=None)
        )
        analysis_completed_at = time.perf_counter()
        wall_seconds = analysis_completed_at - wall_start
        cpu_seconds = time.process_time() - cpu_start
        _current, peak_bytes = tracemalloc.get_traced_memory()
        sampler.sample()
        retained_temp_bytes = _tree_bytes(temp_root)
        source_size = source.stat().st_size
        assert result.matched_occurrences == expected_entries
        assert result.matched_groups == expected_groups
        assert bytes_consumed == source_size
        metrics = {
            "wall_seconds": wall_seconds,
            "process_cpu_seconds": cpu_seconds,
            "python_allocation_peak_bytes": peak_bytes,
            "source_bytes_consumed": bytes_consumed,
            "source_bytes_expected": source_size,
            "matched_occurrences": result.matched_occurrences,
            "matched_groups": result.matched_groups,
            "temp_file_bytes_at_result": retained_temp_bytes,
            "temp_file_bytes_peak_observed": sampler.peak_observed,
            "temp_file_sample_interval_ms": sampler.interval * 1000,
        }
        if append_started is not None:
            metrics["append_to_analysis_result_ms"] = (
                analysis_completed_at - append_started
            ) * 1000
        return metrics
    finally:
        if result is not None:
            result.matched_logs.release()
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        sampler.stop()
        scanner_module.iter_scanned_lines = original_scanner
        tempfile.tempdir = previous_tempdir


class _LivePanelBenchmarkApp(App):
    CSS_PATH = str(_LIVE_CSS)
    CSS = """
    Screen { layout: vertical; }
    #benchmark-host { width: 1fr; height: 1fr; min-width: 0; min-height: 0; }
    """

    def __init__(
        self,
        session: LiveSession,
        line_write_starts: dict[int, float],
        target_sequence: int,
    ) -> None:
        super().__init__()
        self.session = session
        self.line_write_starts = line_write_starts
        self.target_sequence = target_sequence
        self.changed = asyncio.Event()
        self.target_rendered = asyncio.Event()
        self.refresh_completed = asyncio.Event()
        self.boundary_to_callback_latencies: list[float] = []
        self.first_line_to_callback_latencies: list[float] = []
        self.boundary_to_render_latencies: list[float] = []
        self.first_line_to_render_latencies: list[float] = []
        self.render_misses = 0
        self.max_backlog_bytes = 0

    def compose(self) -> ComposeResult:
        with Container(id="benchmark-host"):
            yield LivePanel(id="live-panel")

    def on_mount(self) -> None:
        self.query_one(LivePanel).set_session(self.session)

    def on_events(self, events: list[dict[str, Any]]) -> None:
        if not self.is_running:
            return
        self._refresh_panel()
        delivered_at = time.perf_counter()
        for event in events:
            first_line_started = self.line_write_starts.get(event["line_num"])
            boundary_line_started = self.line_write_starts.get(event["line_num"] + 1)
            if first_line_started is None or boundary_line_started is None:
                raise AssertionError(
                    f"missing append timestamps for event line {event['line_num']}"
                )
            self.boundary_to_callback_latencies.append(
                delivered_at - boundary_line_started
            )
            self.first_line_to_callback_latencies.append(
                delivered_at - first_line_started
            )
        last_event = events[-1]
        self.call_after_refresh(
            self._record_panel_refresh_ready,
            int(last_event["sequence"]),
            int(last_event["generation"]),
            self.line_write_starts[last_event["line_num"] + 1],
            self.line_write_starts[last_event["line_num"]],
        )
        self.changed.set()

    def on_update(self) -> None:
        if not self.is_running:
            return
        self._refresh_panel()
        self.changed.set()

    def _refresh_panel(self) -> None:
        if not self.is_running:
            return
        self.max_backlog_bytes = max(
            self.max_backlog_bytes,
            int(self.session.backlog),
        )
        panel = self.query_one(LivePanel)
        if panel.is_mounted:
            panel.refresh_session()

    def _record_panel_refresh_ready(
        self,
        sequence: int,
        generation: int,
        boundary_line_started: float,
        first_line_started: float,
    ) -> None:
        options = self.query_one("#live-events", OptionList).options
        suffix = f":{generation}:{sequence}"
        if any((option.id or "").endswith(suffix) for option in options):
            refreshed_at = time.perf_counter()
            self.boundary_to_render_latencies.append(
                refreshed_at - boundary_line_started
            )
            self.first_line_to_render_latencies.append(
                refreshed_at - first_line_started
            )
            if sequence == self.target_sequence:
                self.target_rendered.set()
        else:
            self.render_misses += 1
        self.changed.set()

    def mark_refresh_completed(self) -> None:
        self.refresh_completed.set()


async def _wait_for_state(
    app: _LivePanelBenchmarkApp,
    predicate,
    description: str,
    timeout: float,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        app.changed.clear()
        if predicate():
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {description}")
        try:
            await asyncio.wait_for(app.changed.wait(), remaining)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Timed out waiting for {description}") from exc


async def _run_live_once(  # noqa: PLR0915 - one run coordinates reader, writer, runner, and panel metrics
    source: Path,
    chunks: list[bytes],
    interval_ms: float,
    expected_entries: int,
    temp_root: Path,
) -> tuple[dict[str, Any], LiveSession]:
    from log_analyzer.application.live import session as session_module

    source.parent.mkdir(parents=True, exist_ok=True)
    source.touch(exist_ok=True)
    line_write_starts: dict[int, float] = {}
    conditions = AnalysisRequest(str(source), keyword=_KEYWORD, levels=None)
    storage_root = temp_root / "live-storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    session = LiveSession(conditions, directory=storage_root / "events")
    reader_type = session_module.IncrementalFileReader
    original_read = reader_type.read
    bytes_consumed = 0

    def counted_read(reader, *args, **kwargs):
        nonlocal bytes_consumed
        batch = original_read(reader, *args, **kwargs)
        bytes_consumed += len(batch.data)
        return batch

    reader_type.read = counted_read
    sampler = _TemporaryBytesSampler(storage_root)
    sampler.start()
    app = _LivePanelBenchmarkApp(
        session,
        line_write_starts,
        target_sequence=expected_entries,
    )
    writer_report: dict[str, Any] | None = None
    runner: LiveRunner | None = None
    completed = False
    try:
        async with app.run_test(size=(140, 44)):
            runner = LiveRunner(
                session,
                on_events=app.on_events,
                on_update=app.on_update,
            )
            tracemalloc.start()
            wall_start = time.perf_counter()
            cpu_start = time.process_time()
            await runner.start()
            writer_report = await asyncio.to_thread(
                _write_chunks,
                source,
                chunks,
                interval_ms,
                line_write_starts,
            )
            timeout = max(10.0, writer_report["elapsed_seconds"] + 10.0)
            expected_bytes = sum(map(len, chunks))
            await _wait_for_state(
                app,
                lambda: (
                    session.offset == expected_bytes
                    and session.backlog == 0
                    and session.matched == expected_entries
                ),
                "live reader to consume the fixed source cutoff",
                timeout,
            )
            await _wait_for_state(
                app,
                lambda: app.target_rendered.is_set(),
                "the final event to appear after a mounted LivePanel refresh",
                timeout,
            )
            assert runner.failure is None, runner.failure
            assert session.received_bytes == expected_bytes
            assert session.completed == expected_entries
            assert session.has_pending
            snapshot = session.snapshot()
            assert snapshot.cutoff == expected_entries
            assert snapshot.has_pending
            snapshot.release()
            await runner.pause()
            app.refresh_completed.clear()
            app.call_after_refresh(app.mark_refresh_completed)
            await asyncio.wait_for(app.refresh_completed.wait(), timeout=5)
            wall_seconds = time.perf_counter() - wall_start
            cpu_seconds = time.process_time() - cpu_start
            _current, peak_bytes = tracemalloc.get_traced_memory()
            sampler.sample()
            current_temp_bytes = _tree_bytes(storage_root)
            metrics = {
                "wall_seconds": wall_seconds,
                "process_cpu_seconds": cpu_seconds,
                "python_allocation_peak_bytes": peak_bytes,
                "source_bytes_consumed": bytes_consumed,
                "source_bytes_expected": expected_bytes,
                "matched_occurrences": session.matched,
                "completed_events_before_pending_tail": session.completed,
                "pending_tail_excluded": session.has_pending,
                "backlog_bytes_after_read": session.backlog,
                "backlog_bytes_peak_observed": app.max_backlog_bytes,
                "event_boundary_append_to_callback_ms": _latency_stats(
                    app.boundary_to_callback_latencies
                ),
                "first_line_append_to_event_callback_ms": _latency_stats(
                    app.first_line_to_callback_latencies
                ),
                "event_boundary_append_to_panel_refresh_ready_ms": _latency_stats(
                    app.boundary_to_render_latencies
                ),
                "first_line_append_to_panel_refresh_ready_ms": _latency_stats(
                    app.first_line_to_render_latencies
                ),
                "panel_render_callbacks_not_visible": app.render_misses,
                "event_store_temp_file_bytes": _tree_bytes(storage_root / "events"),
                "temp_file_bytes_at_pause": current_temp_bytes,
                "temp_file_bytes_peak_observed": sampler.peak_observed,
                "temp_file_sample_interval_ms": sampler.interval * 1000,
                "write_profile": writer_report,
            }
            assert bytes_consumed == expected_bytes
            completed = True
            return metrics, session
    finally:
        if runner is not None and runner.running:
            await runner.pause()
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        sampler.stop()
        reader_type.read = original_read
        if not completed:
            session.close()


def _measure_summary(  # noqa: PLR0913, PLR0915 - isolates summary IO guards and accounting
    session: LiveSession,
    source: Path,
    expected_entries: int,
    expected_groups: int,
    temp_root: Path,
    live_bytes_before: int,
) -> dict[str, Any]:
    original_store = live_summary_module.LiveSummaryStore
    original_scanner = scanner_module.iter_scanned_lines
    original_open = getattr(scanner_module, "open", builtins.open)
    original_reader = incremental_module.IncrementalFileReader.read
    had_scanner_open = hasattr(scanner_module, "open")
    read_attempts = {
        "scanner_iterator": 0,
        "scanner_open": 0,
        "incremental_reader": 0,
    }
    summary_root = temp_root / "summary-storage"
    summary_root.mkdir(parents=True, exist_ok=True)

    def create_summary_store():
        return original_store(directory=summary_root)

    def is_source(path: Any) -> bool:
        try:
            return Path(path).resolve() == source.resolve()
        except (OSError, TypeError, ValueError):
            return False

    def guarded_scanner(filepath, *args, **kwargs):
        if is_source(filepath):
            read_attempts["scanner_iterator"] += 1
            raise AssertionError("snapshot summary attempted a scanner source read")
        return original_scanner(filepath, *args, **kwargs)

    def guarded_open(file, *args, **kwargs):
        if is_source(file):
            read_attempts["scanner_open"] += 1
            raise AssertionError("snapshot summary attempted to open the source log")
        return original_open(file, *args, **kwargs)

    def guarded_incremental_read(reader, *args, **kwargs):
        if is_source(reader.path):
            read_attempts["incremental_reader"] += 1
            raise AssertionError("snapshot summary attempted an incremental source read")
        return original_reader(reader, *args, **kwargs)

    live_summary_module.LiveSummaryStore = create_summary_store
    scanner_module.iter_scanned_lines = guarded_scanner
    scanner_module.open = guarded_open
    incremental_module.IncrementalFileReader.read = guarded_incremental_read
    sampler = _TemporaryBytesSampler(temp_root)
    sampler.start()
    tracemalloc.start()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    result = None
    snapshot = None
    try:
        snapshot = session.snapshot()
        result = live_summary_module.build_tracking_summary(snapshot)
        wall_seconds = time.perf_counter() - wall_start
        cpu_seconds = time.process_time() - cpu_start
        _current, peak_bytes = tracemalloc.get_traced_memory()
        sampler.sample()
        summary_file_bytes = _tree_bytes(summary_root)
        combined_temp_bytes = _tree_bytes(temp_root)
        session_byte_delta = session.received_bytes - live_bytes_before
        assert result.matched_occurrences == expected_entries
        assert result.matched_groups == expected_groups
        assert snapshot.has_pending
        assert session_byte_delta == 0
        assert all(attempt_count == 0 for attempt_count in read_attempts.values())
        return {
            "wall_seconds": wall_seconds,
            "process_cpu_seconds": cpu_seconds,
            "python_allocation_peak_bytes": peak_bytes,
            "source_bytes_read_during_summary": 0,
            "source_read_attempts_during_summary": read_attempts,
            "session_received_bytes_delta_during_summary": session_byte_delta,
            "matched_occurrences": result.matched_occurrences,
            "matched_groups": result.matched_groups,
            "snapshot_cutoff": snapshot.cutoff,
            "pending_tail_excluded": snapshot.has_pending,
            "summary_temp_file_bytes": summary_file_bytes,
            "combined_temp_file_bytes": combined_temp_bytes,
            "temp_file_bytes_peak_observed": sampler.peak_observed,
            "temp_file_sample_interval_ms": sampler.interval * 1000,
        }
    finally:
        if result is not None:
            result.matched_logs.release()
        elif snapshot is not None:
            snapshot.release()
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        sampler.stop()
        live_summary_module.LiveSummaryStore = original_store
        scanner_module.iter_scanned_lines = original_scanner
        if had_scanner_open:
            scanner_module.open = original_open
        else:
            del scanner_module.open
        incremental_module.IncrementalFileReader.read = original_reader


def _run_batch(
    source: Path,
    entries: int,
    groups: int,
    repeats: int,
    temp_root: Path,
) -> list[dict[str, Any]]:
    runs = []
    for index in range(repeats):
        runs.append(
            _measure_batch_once(
                source,
                entries,
                groups,
                temp_root / f"run-{index + 1}",
            )
        )
    return runs


async def _run_online_batch_baseline(  # noqa: PLR0913 - benchmark inputs stay explicit
    source: Path,
    chunks: list[bytes],
    *,
    total_entries: int,
    chunk_entries: int,
    interval_ms: float,
    temp_root: Path,
) -> dict[str, Any]:
    """按固定節奏追加並在每批後同步重掃目前完整來源。

    Args:
        source: 線上批次基準使用的合成來源檔。
        chunks: 與其他工作流程共用的固定 UTF-8 追加資料。
        total_entries: 來源中符合關鍵字的事件總數。
        chunk_entries: 每個一般 chunk 含有的匹配事件數。
        interval_ms: 預定 chunk 追加間隔。
        temp_root: 保存各次掃描暫存檔的隔離目錄。

    Returns:
        dict[str, Any]: 每次掃描、重讀位元組、CPU、延遲與排程落後統計。
    """
    source.parent.mkdir(parents=True, exist_ok=True)
    source.touch(exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    sampler = _TemporaryBytesSampler(temp_root)
    sampler.start()
    scan_runs: list[dict[str, Any]] = []
    append_times: list[float] = []
    schedule_lags: list[float] = []
    written_bytes = 0
    completed_entries = 0
    schedule_start = time.perf_counter()
    wall_start = schedule_start
    cpu_start = time.process_time()
    interval_seconds = interval_ms / 1000
    try:
        with source.open("ab") as handle:
            for index, chunk in enumerate(chunks):
                due = schedule_start + index * interval_seconds
                delay = due - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)

                append_started = time.perf_counter()
                append_times.append(append_started)
                schedule_lags.append(max(0.0, append_started - due))
                handle.write(chunk)
                handle.flush()
                written_bytes += len(chunk)
                entries_in_chunk = min(
                    chunk_entries,
                    total_entries - completed_entries,
                )
                completed_entries += entries_in_chunk
                scan = _measure_batch_once(
                    source,
                    completed_entries,
                    completed_entries // 2 + (1 if completed_entries else 0),
                    temp_root / f"scan-{index + 1}",
                    append_started,
                )
                scan_runs.append(scan)
                sampler.sample()

        expected_bytes = sum(map(len, chunks))
        assert written_bytes == expected_bytes
        assert completed_entries == total_entries
        assert source.stat().st_size == expected_bytes
        actual_intervals = [
            later - earlier
            for earlier, later in zip(append_times, append_times[1:])
        ]
        return {
            "wall_seconds_including_paced_appends_and_rescans": (
                time.perf_counter() - wall_start
            ),
            "process_cpu_seconds_including_rescans": time.process_time() - cpu_start,
            "total_scan_wall_seconds": sum(
                run["wall_seconds"] for run in scan_runs
            ),
            "total_scan_process_cpu_seconds": sum(
                run["process_cpu_seconds"] for run in scan_runs
            ),
            "python_allocation_peak_bytes_max_single_scan": max(
                run["python_allocation_peak_bytes"] for run in scan_runs
            ),
            "source_bytes_written": written_bytes,
            "total_repeated_scan_source_bytes_consumed": sum(
                run["source_bytes_consumed"] for run in scan_runs
            ),
            "source_bytes_expected_per_final_scan": expected_bytes,
            "scan_count": len(scan_runs),
            "scans": scan_runs,
            "writer_scheduled_interval_ms": interval_ms,
            "writer_schedule_lag_ms": _latency_stats(schedule_lags),
            "actual_append_intervals_ms": _latency_stats(actual_intervals),
            "temp_file_bytes_persisted_after_scan_cleanup": _tree_bytes(temp_root),
            "temp_file_bytes_peak_observed": sampler.peak_observed,
            "temp_file_sample_interval_ms": sampler.interval * 1000,
        }
    finally:
        sampler.stop()


async def _benchmark_async(
    *,
    entries: int,
    batch_repeats: int,
    chunk_entries: int,
    interval_ms: float,
) -> dict[str, Any]:
    chunks = _build_chunks(entries, chunk_entries)
    unique_messages = entries // 2
    expected_groups = unique_messages + 1
    source_root = Path(tempfile.mkdtemp(prefix="live-workflow-source-"))
    temp_root = Path(tempfile.mkdtemp(prefix="live-workflow-temp-"))
    try:
        batch_source = source_root / "batch" / "application.log"
        online_batch_source = source_root / "online-batch" / "application.log"
        live_source = source_root / "live" / "application.log"
        batch_write = _write_chunks(batch_source, chunks, interval_ms)
        batch_runs = _run_batch(
            batch_source,
            entries,
            expected_groups,
            batch_repeats,
            temp_root / "batch",
        )
        online_batch = await _run_online_batch_baseline(
            online_batch_source,
            chunks,
            total_entries=entries,
            chunk_entries=chunk_entries,
            interval_ms=interval_ms,
            temp_root=temp_root / "online-batch",
        )

        live_metrics, session = await _run_live_once(
            live_source,
            chunks,
            interval_ms,
            entries,
            temp_root,
        )
        try:
            live_bytes_before_summary = session.received_bytes
            summary_metrics = _measure_summary(
                session,
                live_source,
                entries,
                expected_groups,
                temp_root,
                live_bytes_before_summary,
            )
        finally:
            session.close()
        source_bytes = batch_source.stat().st_size
        assert live_source.stat().st_size == source_bytes
        assert online_batch_source.stat().st_size == source_bytes
        assert all(run["source_bytes_consumed"] == source_bytes for run in batch_runs)
        assert online_batch["scans"][-1]["matched_occurrences"] == entries
        assert online_batch["scans"][-1]["matched_groups"] == expected_groups
        return {
            "python": sys.version,
            "platform": platform.platform(),
            "workload": {
                "entries": entries,
                "repeated_message_entries": (entries + 1) // 2,
                "unique_message_entries": entries // 2,
                "groups_expected": expected_groups,
                "chunk_entries": chunk_entries,
                "write_interval_ms": interval_ms,
                "chunk_count": len(chunks),
                "source_bytes": source_bytes,
                "source_encoding": "UTF-8, no BOM, LF line endings",
                "filter_keyword": _KEYWORD,
            },
            "write_profiles": {
                "batch_source": batch_write,
                "online_batch_source": {
                    "bytes_written": online_batch["source_bytes_written"],
                    "elapsed_seconds": online_batch[
                        "wall_seconds_including_paced_appends_and_rescans"
                    ],
                    "observed_bytes_per_second": online_batch[
                        "source_bytes_written"
                    ]
                    / online_batch[
                        "wall_seconds_including_paced_appends_and_rescans"
                    ],
                    "scheduled_interval_ms": interval_ms,
                    "observed_intervals_ms": online_batch[
                        "actual_append_intervals_ms"
                    ],
                    "schedule_lag_ms": online_batch["writer_schedule_lag_ms"],
                    "chunks": online_batch["scan_count"],
                },
                "live_source": live_metrics["write_profile"],
            },
            "batch_full_rescan": {
                "runs": batch_runs,
                "median_wall_seconds": statistics.median(
                    run["wall_seconds"] for run in batch_runs
                ),
                "total_source_bytes_consumed": sum(
                    run["source_bytes_consumed"] for run in batch_runs
                ),
            },
            "paced_online_batch_rescan": online_batch,
            "live_incremental_events_and_panel": live_metrics,
            "on_demand_snapshot_summary": summary_metrics,
            "method": [
                "The batch source and live source contain identical generated bytes and use the same paced chunk schedule; batch full scans run against the completed immutable copy.",
                "Batch source bytes are counted from every line yielded by the production scanner and reversibly UTF-8 encoded; the generated UTF-8/LF corpus has no BOM or invalid bytes.",
                "The paced online batch baseline appends the same fixed chunks and synchronously runs a full production analysis after every append. It reports every cumulative rescan's bytes, CPU, wall and append-to-result latency.",
                "Online batch schedule lag compares each actual append start with its fixed target time; synchronous scans run on the producer event loop and therefore delay later appends.",
                "Live source bytes are counted from the exact byte batches returned by IncrementalFileReader.read.",
                "A final standalone BOUNDARY chunk is filtered out by the fixed keyword. It finalizes the final matching event but remains pending in the live parser, so batch and live results exclude the same tail. Earlier chunk-ending events remain pending until a later chunk arrives.",
                "The live phase uses LiveRunner, LiveSession, and a mounted standalone LivePanel with the existing live-panel CSS. Panel readiness is measured after Textual's next refresh callback, not physical terminal transmission.",
                "Temporary file sizes come from stat on isolated temporary directories. Peak values are the largest sampled totals, not guaranteed instantaneous peaks; Python allocation peaks use tracemalloc and are not RSS.",
            ],
            "limitations": [
                "The full batch scans are repeated on the completed immutable copy rather than competing with the paced writer; input bytes and the writer schedule match the live run, while wall times represent different workflows.",
                "The online baseline models a synchronous single-threaded producer; its scan blocks the next scheduled append. This does not model an external logging process that can write concurrently during a scan.",
                "Headless Textual refresh readiness is not terminal-device paint latency.",
                "Results are diagnostic only; there is no pass threshold and no speedup claim.",
            ],
        }
    finally:
        import shutil

        shutil.rmtree(source_root, ignore_errors=True)
        shutil.rmtree(temp_root, ignore_errors=True)


def benchmark(
    *,
    entries: int,
    batch_repeats: int,
    chunk_entries: int,
    interval_ms: float,
) -> dict[str, Any]:
    _validate_parameters(entries, batch_repeats, chunk_entries, interval_ms)
    return asyncio.run(
        _benchmark_async(
            entries=entries,
            batch_repeats=batch_repeats,
            chunk_entries=chunk_entries,
            interval_ms=interval_ms,
        )
    )


def _validate_parameters(
    entries: int,
    batch_repeats: int,
    chunk_entries: int,
    interval_ms: float,
) -> None:
    """驗證工作量與排程參數，拒絕無法解讀的非有限間隔。

    Args:
        entries: 合成匹配事件數。
        batch_repeats: 完成檔案的全量重掃次數。
        chunk_entries: 每批事件數。
        interval_ms: 批次排程間隔毫秒數。

    Returns:
        None: 參數有效時正常返回。

    Raises:
        ValueError: 工作量不合法或間隔非有限、為負數。
    """
    if entries < 2:
        raise ValueError("entries must be at least 2")
    if batch_repeats <= 0 or chunk_entries <= 0:
        raise ValueError("repeat and chunk sizes must be positive")
    if not math.isfinite(interval_ms) or interval_ms < 0:
        raise ValueError("write interval must be finite and non-negative")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="使用小型驗證資料集")
    parser.add_argument("--entries", type=int)
    parser.add_argument("--batch-repeats", type=int)
    parser.add_argument("--chunk-entries", type=int)
    parser.add_argument("--write-interval-ms", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    entries = args.entries if args.entries is not None else (120 if args.smoke else 2_000)
    batch_repeats = (
        args.batch_repeats
        if args.batch_repeats is not None
        else (1 if args.smoke else 3)
    )
    chunk_entries = (
        args.chunk_entries
        if args.chunk_entries is not None
        else (40 if args.smoke else 100)
    )
    interval_ms = (
        args.write_interval_ms
        if args.write_interval_ms is not None
        else (5.0 if args.smoke else 20.0)
    )
    try:
        _validate_parameters(entries, batch_repeats, chunk_entries, interval_ms)
    except ValueError as error:
        parser.error(str(error))

    report = benchmark(
        entries=entries,
        batch_repeats=batch_repeats,
        chunk_entries=chunk_entries,
        interval_ms=interval_ms,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
