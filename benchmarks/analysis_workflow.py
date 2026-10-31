"""以合成資料量測分析、結果定位與取消反應；不讀取使用者日誌。"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from log_analyzer.application.analysis.service import AnalysisRequest, analyze_logs
from log_analyzer.infrastructure.sources.context_index import SourceContextReader
from log_analyzer.application.sources.preview import read_source_context
from log_analyzer.domain.analysis_control import AnalysisCancelled, AnalysisControl
from log_analyzer.presentation.tui.screens.event_browser import EventBrowserScreen


def _write_case(root: Path, name: str, entries: int) -> tuple[Path, int]:
    directory = root / name
    directory.mkdir()
    file_count = 10 if name == "multiple_files" else 1
    per_file = max(1, entries // file_count)
    if name == "repeated_group":
        per_file = entries * 5
    start = datetime(2026, 10, 24)
    for file_index in range(file_count):
        with (directory / f"app-{file_index:02}.log").open("w", encoding="utf-8") as handle:
            for line in range(per_file):
                index = file_index * per_file + line
                timestamp = (start + timedelta(seconds=index)).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
                message = f"failure-{index}" if name == "unique_groups" else "Repeated failure"
                handle.write(f"{timestamp} [worker-{index % 16}] ERROR Service - {message}\n")
    return directory, per_file * file_count


def _timed(call):
    started = time.perf_counter()
    value = call()
    return value, time.perf_counter() - started


def _measure_once(directory: Path, entries: int) -> dict:
    tracemalloc.start()
    started = time.perf_counter()
    result = analyze_logs(AnalysisRequest(str(directory)))
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    try:
        assert result.matched_occurrences == entries
        assert sum(item[2] for item in result.dashboard_summary.time_hotspots) == entries
        assert sum(item[2] for item in result.dashboard_summary.thread_summary) == entries
        _, first_page = _timed(lambda: list(result.matched_logs.iter_page(0, 100)))
        last, last_page = _timed(lambda: list(result.matched_logs.iter_page(max(0, len(result.matched_logs) - 100), 100)))
        occurrences = last[-1]["occurrences"]
        carrier = SimpleNamespace(_occurrences=occurrences)
        order, sorting = _timed(lambda: sorted(
            range(len(occurrences)),
            key=lambda index: EventBrowserScreen._occurrence_sort_key(carrier, index),
        ))
        occurrence = occurrences[order[-1]]
        reader = SourceContextReader()
        snapshot = result.source_states[occurrence["filename"]]
        preview, first_preview = _timed(lambda: read_source_context(
            str(directory), occurrence, snapshot, reader=reader
        ))
        assert preview.warning is None and preview.lines
        nearby = dict(occurrence, line_number=max(1, occurrence["line_number"] - 1))
        preview, cached_preview = _timed(lambda: read_source_context(
            str(directory), nearby, snapshot, reader=reader
        ))
        assert preview.warning is None and preview.lines
        reader.clear()
        return {
            "analysis_seconds": elapsed,
            "python_peak_mib": peak / 1024 ** 2,
            "groups": result.matched_groups,
            "first_group_page_seconds": first_page,
            "last_group_page_seconds": last_page,
            "occurrence_sort_seconds": sorting,
            "sorted_occurrences": len(occurrences),
            "first_source_preview_seconds": first_preview,
            "nearby_source_preview_seconds": cached_preview,
        }
    finally:
        result.matched_logs.release()


def _cancel_latency(directory: Path, entries: int) -> float:
    cancelled_at = None

    def progress(update):
        nonlocal cancelled_at
        if cancelled_at is None and update.lines_processed >= min(128, entries):
            cancelled_at = time.perf_counter()
            control.cancel()

    control = AnalysisControl(progress)
    try:
        result = analyze_logs(AnalysisRequest(str(directory)), control=control)
    except AnalysisCancelled:
        assert cancelled_at is not None
        return time.perf_counter() - cancelled_at
    result.matched_logs.release()
    raise AssertionError("analysis did not observe cancellation")


def benchmark(entries: int, repeat: int) -> dict:
    cases = []
    with tempfile.TemporaryDirectory(prefix="log-analyzer-benchmark-") as root:
        for name in ("multiple_files", "unique_groups", "repeated_group"):
            directory, count = _write_case(Path(root), name, entries)
            runs = []
            for index in range(repeat):
                print(f"Measuring {name}: {count} entries, run {index + 1}/{repeat}", file=sys.stderr, flush=True)
                runs.append(_measure_once(directory, count))
            cancel = [_cancel_latency(directory, count) for _ in range(repeat)]
            cases.append({
                "name": name, "entries": count,
                "source_bytes": sum(path.stat().st_size for path in directory.iterdir()),
                "runs": runs,
                "median": {key: statistics.median(run[key] for run in runs) for key in runs[0]},
                "cancel_latency_seconds": cancel,
            })
    return {
        "python": platform.python_version(), "platform": platform.platform(),
        "repeat": repeat,
        "notes": "Synthetic data; warm filesystem cache. Analysis includes tracemalloc overhead; peak is Python allocations, not process RSS. Occurrence sorting measures the browser algorithm without rendering. Cancellation is measured while scanning; other phases may differ.",
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=20_000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.entries <= 0 or args.repeat <= 0:
        parser.error("entries and repeat must be positive")
    report = benchmark(args.entries, args.repeat)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
