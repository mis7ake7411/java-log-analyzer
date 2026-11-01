from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from os import stat_result
from pathlib import Path
from typing import TextIO

from log_analyzer.domain.text_decoding import (
    SOURCE_TEXT_ENCODING,
    SOURCE_TEXT_ERRORS,
    SOURCE_TEXT_NEWLINE,
    normalize_decoded_line,
)

_INDEX_STRIDE = 256
_MAX_INDEXED_FILES = 16


@dataclass(slots=True)
class _SourceIndex:
    metadata: tuple[int, int, int, int]
    offsets: dict[int, int] = field(default_factory=lambda: {1: 0})
    indexed_through: int = 0
    next_offset: int = 0
    eof_line: int | None = None


@dataclass(frozen=True, slots=True)
class _ReadWindow:
    first_line: int
    last_line: int
    start_line: int
    start_offset: int


def _metadata_key(source_stat: stat_result) -> tuple[int, int, int, int]:
    return (
        source_stat.st_size,
        source_stat.st_mtime_ns,
        source_stat.st_dev,
        source_stat.st_ino,
    )


class SourceContextReader:
    """以稀疏文字串流 seek cookie 重複讀取已核對來源的行內容。"""

    def __init__(self) -> None:
        self._entries: OrderedDict[Path, _SourceIndex] = OrderedDict()

    def clear(self) -> None:
        self._entries.clear()

    def invalidate(self, source: Path) -> None:
        self._entries.pop(source, None)

    def read_context(
        self,
        source: Path,
        source_stat: stat_result,
        first_line: int,
        last_line: int,
    ) -> tuple[tuple[int, str], ...]:
        lines, _has_encoding_error = self.read_context_with_encoding(
            source,
            source_stat,
            first_line,
            last_line,
        )
        return lines

    def read_context_with_encoding(
        self,
        source: Path,
        source_stat: stat_result,
        first_line: int,
        last_line: int,
    ) -> tuple[tuple[tuple[int, str], ...], bool]:
        index = self._index_for(source, source_stat)
        if index.eof_line is not None and first_line > index.eof_line:
            return (), False

        window = self._read_window_for(index, first_line, last_line)
        return self._read_lines(source, index, window)

    def _index_for(self, source: Path, source_stat: stat_result) -> _SourceIndex:
        metadata = _metadata_key(source_stat)
        index = self._entries.get(source)
        if index is not None and index.metadata != metadata:
            self.invalidate(source)
            index = None

        if index is None:
            index = _SourceIndex(metadata)
            self._entries[source] = index
            while len(self._entries) > _MAX_INDEXED_FILES:
                self._entries.popitem(last=False)
        else:
            self._entries.move_to_end(source)
        return index

    @staticmethod
    def _read_window_for(
        index: _SourceIndex,
        first_line: int,
        last_line: int,
    ) -> _ReadWindow:
        if first_line > index.indexed_through:
            start_line = index.indexed_through + 1
            start_offset = index.next_offset
        else:
            start_line = ((first_line - 1) // _INDEX_STRIDE) * _INDEX_STRIDE + 1
            start_offset = index.offsets[start_line]
        return _ReadWindow(first_line, last_line, start_line, start_offset)

    def _read_lines(
        self,
        source: Path,
        index: _SourceIndex,
        window: _ReadWindow,
    ) -> tuple[tuple[tuple[int, str], ...], bool]:
        lines: list[tuple[int, str]] = []
        has_encoding_error = False
        index_extended = False
        with source.open(
            "r",
            encoding=SOURCE_TEXT_ENCODING,
            errors=SOURCE_TEXT_ERRORS,
            newline=SOURCE_TEXT_NEWLINE,
        ) as file_handle:
            file_handle.seek(window.start_offset)
            line_number = window.start_line
            while line_number <= window.last_line:
                text = file_handle.readline()
                if not text:
                    self._record_eof(index, line_number - 1, file_handle)
                    break

                normalized_text, line_has_encoding_error = normalize_decoded_line(text)
                if line_number >= window.first_line:
                    has_encoding_error |= line_has_encoding_error
                    lines.append((line_number, normalized_text.rstrip("\r\n")))

                index_extended |= self._record_indexed_line(index, line_number, file_handle)
                line_number += 1

            if index_extended:
                index.next_offset = file_handle.tell()

        return tuple(lines), has_encoding_error

    @staticmethod
    def _record_indexed_line(
        index: _SourceIndex,
        line_number: int,
        file_handle: TextIO,
    ) -> bool:
        if line_number <= index.indexed_through:
            return False
        index.indexed_through = line_number
        next_line = line_number + 1
        if (next_line - 1) % _INDEX_STRIDE == 0:
            index.offsets[next_line] = file_handle.tell()
        return True

    @staticmethod
    def _record_eof(index: _SourceIndex, eof_line: int, file_handle: TextIO) -> None:
        index.eof_line = eof_line
        if eof_line < index.indexed_through:
            index.indexed_through = eof_line
            index.offsets = {
                indexed_line: offset
                for indexed_line, offset in index.offsets.items()
                if indexed_line <= eof_line
            }
        index.next_offset = file_handle.tell()
