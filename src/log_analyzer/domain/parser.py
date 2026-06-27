from __future__ import annotations

import heapq
import os
import pickle
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .logback_pattern import DEFAULT_LOGBACK_REGEX, compile_logback_pattern
from .parser_aggregation import commit_entry, normalize_keyword, sort_key

_STACKTRACE_HEADER_RE = re.compile(r"^[A-Za-z_][\w.$]*(?:Exception|Error)(?::|\s|$)")
MAX_SORT_GROUPS_IN_MEMORY = 5000
MAX_GROUPS_IN_MEMORY = 5000
MAX_GROUP_SHARDS = 32


@dataclass(slots=True)
class MatchedLogsStore:
    path: str
    length: int
    _index_cache: dict[int, object] | None = None

    def __iter__(self):
        with open(self.path, "rb") as file_handle:
            index = 0
            while True:
                try:
                    log = pickle.load(file_handle)
                except EOFError:
                    break
                self._remember(index, log)
                yield log
                index += 1

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if index < 0:
            index += self.length
        if index < 0 or index >= self.length:
            raise IndexError(index)

        cache = self._index_cache or {}
        if index in cache:
            return cache[index]

        with open(self.path, "rb") as file_handle:
            current_index = 0
            while True:
                try:
                    log = pickle.load(file_handle)
                except EOFError:
                    break
                if current_index == index:
                    self._remember(current_index, log)
                    return log
                self._remember(current_index, log)
                current_index += 1
        raise IndexError(index)

    def __bool__(self):
        return self.length > 0

    def __eq__(self, other):
        if isinstance(other, list):
            return list(self) == other
        return NotImplemented

    def __del__(self):
        self.release()

    def release(self):
        try:
            if self.path and os.path.exists(self.path):
                os.unlink(self.path)
        except OSError:
            pass
        self._index_cache = {}

    def _remember(self, index: int, log) -> None:
        if self._index_cache is None:
            self._index_cache = {}
        self._index_cache[index] = log


def parse_logs(
    directory,
    start_time=None,
    end_time=None,
    keyword=None,
    ignore_case=False,
    log_pattern=None,
    sort_by="time",
):
    """
    解析指定資料夾中的所有 Logback 日誌檔案。

    回傳:
        tuple: (各等級的統計數量, 所有符合條件的日誌詳情列表)
    """
    counts = Counter()
    search_keyword = normalize_keyword(keyword, ignore_case)
    grouped_logs = {}
    spilled_to_disk = False

    with tempfile.TemporaryDirectory() as temp_dir:
        shard_paths = _build_group_shard_paths(temp_dir)

        for entry in iter_logs(directory, start_time, end_time, log_pattern):
            commit_entry(grouped_logs, counts, entry, search_keyword, ignore_case)
            if len(grouped_logs) >= MAX_GROUPS_IN_MEMORY:
                _spill_grouped_logs(grouped_logs, shard_paths)
                grouped_logs.clear()
                spilled_to_disk = True

        if spilled_to_disk:
            _spill_grouped_logs(grouped_logs, shard_paths)
            grouped_logs.clear()
            sorted_logs = iter_sorted_logs(_load_grouped_logs_from_shards(shard_paths), sort_by)
        else:
            sorted_logs = iter_sorted_logs(grouped_logs.values(), sort_by)

    return counts, _persist_matched_logs(sorted_logs)


def iter_logs(
    directory,
    start_time=None,
    end_time=None,
    log_pattern=None,
):
    """逐筆產生已解析、已套用時間區間過濾的日誌 entry。"""
    entry_pattern = compile_logback_pattern(log_pattern) if log_pattern else DEFAULT_LOGBACK_REGEX

    if not os.path.exists(directory):
        raise FileNotFoundError(f"找不到目錄: {directory}")

    for filename in _list_log_files(directory):
        filepath = os.path.join(directory, filename)
        yield from _iter_entries_from_file(filepath, filename, entry_pattern, start_time, end_time)


def iter_sorted_logs(entries, sort_by="time", max_in_memory_groups=MAX_SORT_GROUPS_IN_MEMORY):
    """將已分組的 entries 依指定排序輸出，必要時改用外部合併排序。"""
    entries = list(entries)
    if len(entries) <= max_in_memory_groups:
        yield from sorted(entries, key=lambda entry: sort_key(entry, sort_by))
        return

    yield from _iter_sorted_logs_external(entries, sort_by, max_in_memory_groups)


def _build_group_shard_paths(temp_dir):
    return [os.path.join(temp_dir, f"group_shard_{index:03d}.pkl") for index in range(MAX_GROUP_SHARDS)]


def _spill_grouped_logs(grouped_logs, shard_paths):
    if not grouped_logs:
        return

    shard_entries = {}
    for log in grouped_logs.values():
        shard_index = _group_shard_index(log, len(shard_paths))
        shard_entries.setdefault(shard_index, []).append(log)

    for shard_index, entries in shard_entries.items():
        with open(shard_paths[shard_index], "ab") as file_handle:
            for entry in entries:
                pickle.dump(entry, file_handle, protocol=pickle.HIGHEST_PROTOCOL)


def _load_grouped_logs_from_shards(shard_paths):
    grouped_logs = {}

    for path in shard_paths:
        if not os.path.exists(path):
            continue

        with open(path, "rb") as file_handle:
            while True:
                try:
                    entry = pickle.load(file_handle)
                except EOFError:
                    break
                _merge_grouped_log(grouped_logs, entry)

    return grouped_logs.values()


def _merge_grouped_log(grouped_logs, entry):
    key = _group_log_key(entry)

    if key in grouped_logs:
        grouped_logs[key].count += entry.count
        grouped_logs[key].last_timestamp = entry.last_timestamp
        grouped_logs[key].line_numbers = grouped_logs[key].line_numbers + entry.line_numbers
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


def _persist_matched_logs(logs):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as file_handle:
        length = 0
        for log in logs:
            pickle.dump(log, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
            length += 1
        path = file_handle.name

    return MatchedLogsStore(path=path, length=length)


def _iter_sorted_logs_external(entries, sort_by, chunk_size):
    """把大量 entries 切成排序區塊後，透過暫存檔合併輸出。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        chunk_paths = []
        for index in range(0, len(entries), chunk_size):
            chunk = sorted(entries[index:index + chunk_size], key=lambda entry: sort_key(entry, sort_by))
            chunk_path = os.path.join(temp_dir, f"sorted_chunk_{len(chunk_paths):05d}.pkl")
            with open(chunk_path, "wb") as file_handle:
                pickle.dump(chunk, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
            chunk_paths.append(chunk_path)

        iterators = [_load_sorted_chunk(path) for path in chunk_paths]
        yield from heapq.merge(*iterators, key=lambda entry: sort_key(entry, sort_by))


def _load_sorted_chunk(path):
    """讀取單一已排序區塊並逐筆吐出。"""
    with open(path, "rb") as file_handle:
        for entry in pickle.load(file_handle):
            yield entry


def _list_log_files(directory):
    """回傳資料夾內符合副檔名條件的 log 檔案。"""
    return sorted(filename for filename in os.listdir(directory) if filename.endswith(".log"))


def _iter_entries_from_file(
    filepath,
    filename,
    entry_pattern,
    start_time,
    end_time,
):
    """逐行讀取檔案，產生已完成的 log entry。"""
    current_entry = None

    with open(filepath, "r", encoding="utf-8", errors="ignore") as file_handle:
        for line_num, line in enumerate(file_handle, 1):
            match = entry_pattern.match(line)

            if match:
                if current_entry:
                    yield current_entry
                    current_entry = None

                current_entry = _create_entry(match, filename, line_num)
                if current_entry is None:
                    continue

                entry_time = _parse_entry_time(current_entry["timestamp"])
                if entry_time is None:
                    current_entry = None
                    continue

                if start_time and entry_time < start_time:
                    current_entry = None
                    continue
                if end_time and entry_time > end_time:
                    current_entry = None
                    continue
            elif current_entry:
                _append_continuation_line(current_entry, line_num, line)

        if current_entry:
            yield current_entry


def _create_entry(match, filename, line_num):
    """把正則比對結果轉成內部 entry 結構。"""
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


def _parse_entry_time(timestamp_str):
    """從日誌時間戳取出可比較的 datetime。"""
    try:
        return datetime.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _append_continuation_line(entry, line_num, line):
    """把後續續行附加到目前 entry。"""
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
