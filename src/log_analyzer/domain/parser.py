from __future__ import annotations

import heapq
import os
import pickle
import re
import tempfile
from array import array
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .analysis_control import AnalysisControl
from .logback_pattern import DEFAULT_LOGBACK_REGEX, compile_logback_pattern
from .log_sources import select_log_source
from .matched_logs_store import MatchedLogsStore
from log_analyzer.domain.parser_aggregation import commit_entry, normalize_keyword, normalize_line_numbers, sort_key
from log_analyzer.domain.log_types import FileScanCoverage, LogEntry, MatchedLogs, ScanDiagnostics, SourceSnapshot
from .text_decoding import (
    SOURCE_TEXT_ENCODING,
    SOURCE_TEXT_ERRORS,
    SOURCE_TEXT_NEWLINE,
    normalize_decoded_line,
)
from .timezone import to_local_timezone

_STACKTRACE_HEADER_RE = re.compile(r"^[A-Za-z_][\w.$]*(?:Exception|Error)(?::|\s|$)")
_POSSIBLE_LOG_HEADER_RE = re.compile(r"^\s*\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}")
MAX_SORT_GROUPS_IN_MEMORY = 5000
MAX_GROUPS_IN_MEMORY = 5000
MAX_GROUP_SHARDS = 32


@dataclass(frozen=True)
class ParseOptions:
    start_time: datetime | None = None
    end_time: datetime | None = None
    keyword: str | None = None
    ignore_case: bool = False
    log_pattern: str | None = None
    sort_by: str = "time"
    levels: tuple[str, ...] | None = None
    selected_files: tuple[str, ...] | None = None


def parse_logs(  # noqa: PLR0913 - public legacy API retained for backward compatibility
    directory,
    start_time=None,
    end_time=None,
    keyword=None,
    ignore_case=False,
    log_pattern=None,
    sort_by="time",
    levels=None,
):
    """
    解析指定資料夾中的所有 Logback 日誌檔案

    回傳:
        tuple: (各等級的統計數量, 所有符合條件的日誌詳情列表)
    """
    return parse_log_directory(
        directory,
        ParseOptions(
            start_time,
            end_time,
            keyword,
            ignore_case,
            log_pattern,
            sort_by,
            levels,
        ),
    )


def parse_log_directory(
    directory: str,
    options: ParseOptions,
    *,
    control: AnalysisControl | None = None,
) -> tuple[Counter[str], MatchedLogs]:
    if control is not None:
        control.raise_if_cancelled()
    counts = Counter()
    diagnostics = ScanDiagnostics()
    grouped_logs, _spilled_to_disk, source_states = _collect_grouped_logs(
        directory,
        options,
        counts,
        diagnostics,
        control=control,
    )
    sorted_logs = iter_sorted_logs(grouped_logs, options.sort_by, control=control)

    return counts, _persist_matched_logs(sorted_logs, source_states, diagnostics, control=control)


def _collect_grouped_logs(directory, options, counts, diagnostics=None, *, control=None):
    search_keyword = normalize_keyword(options.keyword, options.ignore_case)
    selected_levels = _normalize_levels(options.levels)
    grouped_logs = {}
    spilled_to_disk = False
    source_states: dict[str, SourceSnapshot] = {}

    with tempfile.TemporaryDirectory() as temp_dir:
        shard_paths = _build_group_shard_paths(temp_dir)
        _report_phase(control, "grouping")

        for entry in iter_logs(
            directory,
            options.start_time,
            options.end_time,
            options.log_pattern,
            source_states,
            diagnostics=diagnostics,
            selected_files=options.selected_files,
            control=control,
        ):
            _raise_if_cancelled(control)
            if selected_levels is not None and entry["level"].upper() not in selected_levels:
                continue
            if diagnostics is not None:
                diagnostics.after_level += 1
            if commit_entry(grouped_logs, counts, entry, search_keyword, options.ignore_case):
                if diagnostics is not None:
                    diagnostics.after_keyword += 1
            if len(grouped_logs) >= MAX_GROUPS_IN_MEMORY:
                _spill_grouped_logs(grouped_logs, shard_paths, control=control)
                grouped_logs.clear()
                spilled_to_disk = True

        if spilled_to_disk:
            _spill_grouped_logs(grouped_logs, shard_paths, control=control)
            grouped_logs = _load_grouped_logs_from_shards(shard_paths, control=control)
        else:
            grouped_logs = grouped_logs.values()

        _raise_if_cancelled(control)
    return grouped_logs, spilled_to_disk, source_states


def _normalize_levels(levels):
    if levels is None:
        return None
    return {str(level).strip().upper() for level in levels if str(level).strip()}


def iter_logs(  # noqa: PLR0913 - preserve the existing positional API
    directory,
    start_time=None,
    end_time=None,
    log_pattern=None,
    source_states: dict[str, SourceSnapshot] | None = None,
    *,
    diagnostics: ScanDiagnostics | None = None,
    selected_files: tuple[str, ...] | None = None,
    control: AnalysisControl | None = None,
):
    """逐筆產生已解析、已套用時間區間過濾的日誌 entry"""
    if start_time is not None:
        start_time = to_local_timezone(start_time)
    if end_time is not None:
        end_time = to_local_timezone(end_time)

    entry_pattern = compile_logback_pattern(log_pattern) if log_pattern else DEFAULT_LOGBACK_REGEX
    if source_states is None:
        source_states = {}

    if not os.path.exists(directory):
        raise FileNotFoundError(f"找不到目錄: {directory}")

    source = select_log_source(directory, selected_files=selected_files)
    files_total = len(source.filenames)
    lines_processed = 0
    if control is not None and files_total == 0:
        control.report_progress("scanning", files_completed=0, files_total=0, force=True)

    for file_index, filename in enumerate(source.filenames):
        _raise_if_cancelled(control)
        filepath = os.path.join(source.root, filename)
        before = os.stat(filepath)
        if diagnostics is not None:
            diagnostics.files_scanned += 1
        accumulator = _EntryAccumulator(filename, start_time, end_time, diagnostics=diagnostics)
        if control is not None:
            control.report_progress(
                "scanning",
                filename=filename,
                files_completed=file_index,
                files_total=files_total,
                lines_processed=lines_processed,
                force=True,
            )

        def on_line() -> None:
            nonlocal lines_processed
            lines_processed += 1
            if control is not None:
                control.raise_if_cancelled()
                control.report_progress(
                    "scanning",
                    filename=filename,
                    files_completed=file_index,
                    files_total=files_total,
                    lines_processed=lines_processed,
                )

        file_has_encoding_error = False

        def on_encoding_error() -> None:
            nonlocal file_has_encoding_error
            if diagnostics is None:
                return
            diagnostics.encoding_error_lines += 1
            if not file_has_encoding_error:
                file_has_encoding_error = True
                diagnostics.encoding_error_files += 1

        yield from _iter_entries_from_file(
            filepath,
            entry_pattern,
            accumulator,
            on_line=on_line if control is not None else None,
            on_encoding_error=on_encoding_error if diagnostics is not None else None,
        )
        _raise_if_cancelled(control)
        after = os.stat(filepath)
        source_states[filename] = {
            "size": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "stable": (
                before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns
            ),
        }
        if control is not None:
            control.report_progress(
                "scanning",
                filename=filename,
                files_completed=file_index + 1,
                files_total=files_total,
                lines_processed=lines_processed,
                force=True,
            )


def iter_sorted_logs(
    entries,
    sort_by="time",
    max_in_memory_groups=MAX_SORT_GROUPS_IN_MEMORY,
    *,
    control: AnalysisControl | None = None,
):
    """將已分組的 entries 依指定排序輸出，必要時改用外部合併排序"""
    if control is not None:
        _report_phase(control, "sorting")
    collected_entries = []
    for entry in entries:
        _raise_if_cancelled(control)
        collected_entries.append(entry)
    entries = collected_entries
    _raise_if_cancelled(control)
    if len(entries) <= max_in_memory_groups:
        # Python's built-in sort is a single non-interruptible operation.
        sorted_entries = sorted(entries, key=lambda entry: sort_key(entry, sort_by))
        _raise_if_cancelled(control)
        yield from sorted_entries
        return

    yield from _iter_sorted_logs_external(entries, sort_by, max_in_memory_groups, control=control)


def _build_group_shard_paths(temp_dir):
    return [os.path.join(temp_dir, f"group_shard_{index:03d}.pkl") for index in range(MAX_GROUP_SHARDS)]


def _spill_grouped_logs(grouped_logs, shard_paths, *, control=None):
    if not grouped_logs:
        return

    shard_entries = {}
    for log in grouped_logs.values():
        _raise_if_cancelled(control)
        shard_index = _group_shard_index(log, len(shard_paths))
        shard_entries.setdefault(shard_index, []).append(log)

    for shard_index, entries in shard_entries.items():
        _raise_if_cancelled(control)
        with open(shard_paths[shard_index], "ab") as file_handle:
            for entry in entries:
                _raise_if_cancelled(control)
                pickle.dump(entry, file_handle, protocol=pickle.HIGHEST_PROTOCOL)


def _load_grouped_logs_from_shards(shard_paths, *, control=None):
    grouped_logs = {}

    for path in shard_paths:
        _raise_if_cancelled(control)
        if not os.path.exists(path):
            continue

        with open(path, "rb") as file_handle:
            while True:
                try:
                    entry = pickle.load(file_handle)
                except EOFError:
                    break
                _merge_grouped_log(grouped_logs, entry)
                _raise_if_cancelled(control)

    return grouped_logs.values()


def _merge_grouped_log(grouped_logs, entry):
    key = _group_log_key(entry)

    if key in grouped_logs:
        grouped_logs[key].count += entry.count
        grouped_logs[key].last_timestamp = entry.last_timestamp
        if not isinstance(grouped_logs[key].line_numbers, list):
            grouped_logs[key].line_numbers = normalize_line_numbers(grouped_logs[key].line_numbers)
        grouped_logs[key].line_numbers.extend(normalize_line_numbers(entry.line_numbers))
        grouped_logs[key].occurrences.extend(entry.occurrences)
        return

    grouped_logs[key] = entry


def _group_shard_index(entry, shard_count):
    return hash(_group_log_key(entry)) % shard_count


def _group_log_key(entry):
    return (
        entry.level,
        entry.logger,
        entry.message,
        entry.message_body,
        entry.stacktrace,
    )


def _persist_matched_logs(logs, source_states, diagnostics=None, *, control=None):
    path = None
    try:
        iterator = iter(logs)
        sentinel = object()
        first_log = next(iterator, sentinel)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as file_handle:
            path = file_handle.name
            length = 0
            offsets = array("Q")
            _report_phase(control, "persisting", force=True)
            _raise_if_cancelled(control)
            if first_log is not sentinel:
                _raise_if_cancelled(control)
                offsets.append(file_handle.tell())
                pickle.dump(first_log, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
                length += 1
            for log in iterator:
                _raise_if_cancelled(control)
                offsets.append(file_handle.tell())
                pickle.dump(log, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
                length += 1

        return MatchedLogsStore(
            path=path,
            length=length,
            source_states=source_states,
            diagnostics=diagnostics if diagnostics is not None else ScanDiagnostics(),
            _offsets=offsets,
        )
    except BaseException:
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise


def _iter_sorted_logs_external(entries, sort_by, chunk_size, *, control=None):
    """把大量 entries 切成排序區塊後，透過暫存檔合併輸出"""
    with tempfile.TemporaryDirectory() as temp_dir:
        chunk_paths = []
        for index in range(0, len(entries), chunk_size):
            _raise_if_cancelled(control)
            chunk = sorted(entries[index:index + chunk_size], key=lambda entry: sort_key(entry, sort_by))
            _raise_if_cancelled(control)
            chunk_path = os.path.join(temp_dir, f"sorted_chunk_{len(chunk_paths):05d}.pkl")
            with open(chunk_path, "wb") as file_handle:
                pickle.dump(chunk, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
            chunk_paths.append(chunk_path)

        iterators = [_load_sorted_chunk(path) for path in chunk_paths]
        for entry in heapq.merge(*iterators, key=lambda entry: sort_key(entry, sort_by)):
            _raise_if_cancelled(control)
            yield entry


def _load_sorted_chunk(path):
    """讀取單一已排序區塊並逐筆吐出"""
    with open(path, "rb") as file_handle:
        for entry in pickle.load(file_handle):
            yield entry


def _iter_entries_from_file(
    filepath,
    entry_pattern,
    accumulator: _EntryAccumulator,
    *,
    on_line=None,
    on_encoding_error=None,
):
    """逐行讀取檔案，產生已完成的 log entry"""
    coverage = None
    if accumulator.diagnostics is not None:
        coverage = FileScanCoverage(accumulator.filename)
        accumulator.diagnostics.file_coverage.append(coverage)
    has_header = False
    with open(
        filepath,
        "r",
        encoding=SOURCE_TEXT_ENCODING,
        errors=SOURCE_TEXT_ERRORS,
        newline=SOURCE_TEXT_NEWLINE,
    ) as file_handle:
        for line_num, line in enumerate(file_handle, 1):
            if on_line is not None:
                on_line()
            line, has_encoding_error = normalize_decoded_line(line)
            if has_encoding_error and on_encoding_error is not None:
                on_encoding_error()
            match = entry_pattern.match(line)
            if coverage is not None:
                _record_line_coverage(coverage, line_num, line, bool(match), has_header)

            if match:
                has_header = True
                completed_entry = accumulator.start_entry(match, line_num)
                if completed_entry:
                    yield completed_entry
                continue

            accumulator.append_continuation(line_num, line)

        completed_entry = accumulator.finish()
        if completed_entry:
            yield completed_entry


def _record_line_coverage(coverage, line_num, line, matched, has_header):
    coverage.total_lines += 1
    stripped = line.strip()
    if matched:
        coverage.parsed_entries += 1
    elif not stripped:
        coverage.blank_lines += 1
    elif not has_header or _POSSIBLE_LOG_HEADER_RE.match(line):
        coverage.unrecognized_lines += 1
        if len(coverage.unrecognized_samples) < 3:
            coverage.unrecognized_samples.append((line_num, line.rstrip("\r\n")[:240]))
    elif stripped.startswith(("at ", "Caused by:", "Suppressed:", "... ")) or _STACKTRACE_HEADER_RE.match(stripped):
        coverage.stacktrace_lines += 1
    else:
        coverage.continuation_lines += 1


def _raise_if_cancelled(control) -> None:
    if control is not None:
        control.raise_if_cancelled()


def _report_phase(control, phase: str, *, force: bool = True) -> None:
    if control is not None:
        control.report_progress(phase, force=force)


@dataclass(slots=True)
class _EntryAccumulator:
    """維護單一檔案目前正在累積的 log entry。"""

    filename: str
    start_time: datetime | None
    end_time: datetime | None
    current_entry: LogEntry | None = None
    diagnostics: ScanDiagnostics | None = None

    def start_entry(self, match, line_num):
        completed_entry = self.current_entry
        self.current_entry = _create_entry(match, self.filename, line_num)
        if self.current_entry and self.diagnostics is not None:
            self.diagnostics.parsed_entries += 1
        if self.current_entry and not _is_entry_in_time_range(
            self.current_entry,
            self.start_time,
            self.end_time,
        ):
            self.current_entry = None
        elif self.current_entry and self.diagnostics is not None:
            self.diagnostics.after_time += 1
        return completed_entry

    def append_continuation(self, line_num, line):
        if self.current_entry:
            _append_continuation_line(self.current_entry, line_num, line)

    def finish(self):
        return self.current_entry


def _create_entry(match, filename, line_num):
    """把正則比對結果轉成內部 entry 結構"""
    timestamp_str = match.group("timestamp")
    thread = match.group("thread")
    level = match.group("level")
    logger = match.group("logger")
    message = match.group("message")

    return {
        "timestamp": timestamp_str,
        "thread": thread,
        "level": level,
        "logger": logger,
        "message": message,
        "filename": filename,
        "line_num": line_num,
        "stacktrace_lines": [],
        "message_body": "",
        "full_text": message,
    }


def _is_entry_in_time_range(entry, start_time, end_time):
    entry_time = _parse_entry_time(entry["timestamp"])
    if entry_time is None:
        return False
    if start_time and entry_time < start_time:
        return False
    return not end_time or entry_time <= end_time


def _parse_entry_time(timestamp_str):
    """從日誌時間戳取出可比較的 datetime"""
    try:
        return to_local_timezone(datetime.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _append_continuation_line(entry, line_num, line):
    """把後續續行附加到目前 entry"""
    line_text = line.rstrip("\n")
    entry["full_text"] += f"\n{line_text}"

    if _is_stacktrace_line(entry, line_text):
        entry["stacktrace_lines"].append(f"{line_num:5}: {line}")
        return

    entry["message_body"] = _append_message_body(entry["message_body"], line_text)


def _append_message_body(message_body, line_text):
    if not message_body:
        return line_text
    return f"{message_body}\n{line_text}"


def _is_stacktrace_line(entry, line_text):
    if entry["stacktrace_lines"]:
        return True

    stripped = line_text.strip()
    if not stripped:
        return False

    if stripped.startswith(("at ", "Caused by:", "Suppressed:", "... ")):
        return True

    return bool(_STACKTRACE_HEADER_RE.match(stripped))
