from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from ..domain.log_types import LogEntry
from .export_csv import render_csv_prefix, render_csv_summary, serialize_csv_log
from .export_json import (
    render_json_prefix,
    render_json_summary,
    render_json_suffix,
    serialize_json_log,
)
from .export_markdown import render_markdown_prefix, render_markdown_summary, serialize_markdown_log

MAX_EXPORT_BYTES_PER_FILE = 50 * 1024 * 1024


def export_results(
    counts: Mapping[str, int],
    matched_logs: Iterable[LogEntry],
    output_path: str,
    format: str = "csv",
    max_export_bytes: int | None = None,
    split_by_level: bool = False,
):
    """
    根據指定的格式匯出分析結果

    參數:
        counts (Counter): 統計數據
        matched_logs (list): 符合條件的日誌詳情
        output_path (str): 輸出路徑
        format (str): 格式 ('csv', 'json', 'md')
    """
    output = Path(output_path)
    format_name = format.lower()
    threshold = MAX_EXPORT_BYTES_PER_FILE if max_export_bytes is None else max_export_bytes
    active_levels = [str(level) for level, count in counts.items() if count > 0]
    if split_by_level and len(active_levels) > 1:
        return _export_by_level(counts, matched_logs, output, format_name, threshold, active_levels)

    return _export_streaming(counts, matched_logs, output, format_name, threshold)


def _export_by_level(
    counts: Mapping[str, int],
    matched_logs: Iterable[LogEntry],
    output: Path,
    format_name: str,
    threshold: int,
    active_levels: list[str],
) -> list[str]:
    base_name = output.stem
    suffix = output.suffix or f".{format_name}"
    writers = {
        level: _LevelFileWriter(
            output.with_name(f"{base_name}_{_safe_filename_part(level)}{suffix}"),
            {level: counts[level]},
            format_name,
            threshold,
        )
        for level in active_levels
    }
    created_paths: list[str] = []

    try:
        for log in matched_logs:
            level = str(log.get("level", ""))
            writer = writers.get(level)
            if writer is None:
                continue
            writer.write(log)

        for level in active_levels:
            created_paths.extend(writers[level].close())

        summary_path = output.with_name(f"{base_name}_summary{suffix}")
        _write_text(summary_path, _render_summary(counts, created_paths, format_name), _encoding_for(format_name))
        return [str(summary_path), *created_paths]
    except Exception:
        for writer in writers.values():
            writer.abort()
        for file_path in created_paths:
            _unlink_if_exists(Path(file_path))
        raise


class _LevelFileWriter:
    def __init__(self, output: Path, counts, format_name: str, threshold: int) -> None:
        self.output = output
        self.counts = counts
        self.format_name = format_name
        self.threshold = threshold
        self.base_name = output.stem
        self.suffix = output.suffix or f".{format_name}"
        self.encoding = _encoding_for(format_name)
        self.part_paths: list[Path] = []
        self.current_part_index = 1
        self.current_has_logs = False
        self.current_part_log_index = 0
        self.split_occurred = False
        self.current_path = output
        self.current_handle = _open_part_file(output, counts, format_name, self.encoding)
        self.current_size = _rendered_size(_report_prefix(counts, format_name), format_name)
        self.closed = False

    def write(self, log) -> None:
        while True:
            self.current_part_log_index += 1
            block, block_size = _render_log_block(
                self.current_part_log_index,
                log,
                self.format_name,
                self.current_has_logs,
            )

            if self.current_has_logs and self.current_size + block_size > self.threshold:
                self.current_handle, self.current_path = _rotate_part(
                    self.current_handle,
                    self.current_path,
                    self.output,
                    self.base_name,
                    self.suffix,
                    self.encoding,
                    self.part_paths,
                    self.current_part_index,
                    self.split_occurred,
                    self.counts,
                    self.format_name,
                )
                self.current_part_index += 1
                self.split_occurred = True
                self.current_size = _rendered_size(_report_prefix(self.counts, self.format_name), self.format_name)
                self.current_has_logs = False
                self.current_part_log_index = 0
                continue

            self.current_handle.write(block)
            self.current_size += block_size
            self.current_has_logs = True
            break

    def close(self) -> list[str]:
        if self.closed:
            return []

        self.current_handle = _close_part_file(self.current_handle, self.format_name)
        self.closed = True
        if self.split_occurred:
            self.part_paths.append(self.current_path)
            return [str(path) for path in self.part_paths]
        return [str(self.output)]

    def abort(self) -> None:
        if not self.closed and self.current_handle is not None:
            self.current_handle.close()
            self.closed = True
        _unlink_if_exists(self.output)
        _unlink_if_exists(self.current_path)
        for path in self.part_paths:
            _unlink_if_exists(path)


def _export_streaming(
    counts: Mapping[str, int],
    matched_logs: Iterable[LogEntry],
    output: Path,
    format_name: str,
    threshold: int,
) -> list[str]:
    base_name = output.stem
    suffix = output.suffix or f".{format_name}"
    encoding = _encoding_for(format_name)

    part_paths: list[Path] = []
    current_size = 0
    current_part_index = 1
    current_has_logs = False
    current_part_log_index = 0
    split_occurred = False
    current_handle = _open_part_file(output, counts, format_name, encoding)
    current_path = output
    current_size = _rendered_size(_report_prefix(counts, format_name), format_name)

    iterator = iter(matched_logs)
    has_any_logs = False

    for log in iterator:
        has_any_logs = True
        while True:
            current_part_log_index += 1
            block, block_size = _render_log_block(current_part_log_index, log, format_name, current_has_logs)

            if current_has_logs and current_size + block_size > threshold:
                current_handle, current_path = _rotate_part(
                    current_handle,
                    current_path,
                    output,
                    base_name,
                    suffix,
                    encoding,
                    part_paths,
                    current_part_index,
                    split_occurred,
                    counts,
                    format_name,
                )
                current_part_index += 1
                split_occurred = True
                current_size = _rendered_size(_report_prefix(counts, format_name), format_name)
                current_has_logs = False
                current_part_log_index = 0
                continue

            current_handle.write(block)
            current_size += block_size
            current_has_logs = True
            break

    if format_name == 'md' and not has_any_logs:
        current_handle.write("無符合條件的記錄\n")
        current_size += _rendered_size("無符合條件的記錄\n", format_name)

    if split_occurred:
        current_handle = _close_part_file(current_handle, format_name)
        part_paths.append(current_path)
        summary_path = output.with_name(f"{base_name}_summary{suffix}")
        _write_text(summary_path, _render_summary(counts, [str(path) for path in part_paths], format_name), encoding)
        return [str(summary_path)] + [str(path) for path in part_paths]

    _close_part_file(current_handle, format_name)
    return [str(output)]


def _render_log_block(index: int, log, format_name: str, has_previous_logs: bool) -> tuple[str, int]:
    if format_name == 'json':
        prefix = ",\n" if has_previous_logs else ""
        block = prefix + serialize_json_log(log)
        return block, _rendered_size(block, format_name)
    if format_name == 'md':
        block = serialize_markdown_log(index, log)
        return block, _rendered_size(block, format_name)

    # CSV 以逐行寫入，不需要額外分隔符
    block = serialize_csv_log(log)
    return block, _rendered_size(block, format_name)


def _report_prefix(counts, format_name: str) -> str:
    if format_name == 'json':
        return render_json_prefix(counts)
    if format_name == 'md':
        return render_markdown_prefix(counts)
    return render_csv_prefix(counts)


def _report_suffix(format_name: str) -> str:
    if format_name == 'json':
        return render_json_suffix()
    return ""


def _open_part_file(path: Path, counts, format_name: str, encoding: str):
    file_handle = open(path, "w", encoding=encoding, newline="" if encoding == "utf-8-sig" else None)
    file_handle.write(_report_prefix(counts, format_name))
    return file_handle


def _close_part_file(file_handle, format_name: str):
    file_handle.write(_report_suffix(format_name))
    file_handle.close()


def _rotate_part(
    current_handle,
    current_path: Path,
    output: Path,
    base_name: str,
    suffix: str,
    encoding: str,
    part_paths: list[Path],
    current_part_index: int,
    split_occurred: bool,
    counts,
    format_name: str,
):
    _close_part_file(current_handle, format_name)

    next_part_path = output.with_name(f"{base_name}_part{current_part_index:03d}{suffix}")
    if not split_occurred:
        os.replace(current_path, next_part_path)
    part_paths.append(next_part_path if not split_occurred else current_path)

    new_path = output.with_name(f"{base_name}_part{current_part_index + 1:03d}{suffix}")
    new_handle = _open_part_file(new_path, counts, format_name, encoding)
    return new_handle, new_path


def _render_summary(counts, split_files, format_name: str) -> str:
    if format_name == 'json':
        return render_json_summary(counts, split_files)
    if format_name == 'md':
        return render_markdown_summary(counts, split_files)
    return render_csv_summary(counts, split_files)


def _write_text(output: Path, text: str, encoding: str) -> None:
    with open(output, 'w', encoding=encoding, newline='' if encoding == 'utf-8-sig' else None) as file_handle:
        file_handle.write(text)


def _safe_filename_part(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return safe or "UNKNOWN"


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _encoding_for(format_name: str) -> str:
    return 'utf-8-sig' if format_name == 'csv' else 'utf-8'


def _rendered_size(text: str, format_name: str) -> int:
    return len(text.encode(_encoding_for(format_name)))
