from __future__ import annotations

import os
import pickle
from array import array
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from typing import cast

from .log_types import LogEntry, ScanDiagnostics, SourceSnapshot

_MAX_CACHED_ENTRIES = 64


@dataclass(slots=True)
class MatchedLogsStore:
    path: str
    length: int
    _index_cache: dict[int, LogEntry] | None = None
    source_states: dict[str, SourceSnapshot] = field(default_factory=dict)
    diagnostics: ScanDiagnostics = field(default_factory=ScanDiagnostics)
    _offsets: array | None = None

    def __post_init__(self) -> None:
        if self._index_cache is not None:
            while len(self._index_cache) > _MAX_CACHED_ENTRIES:
                del self._index_cache[next(iter(self._index_cache))]

        if self._offsets is not None:
            if not isinstance(self._offsets, array) or self._offsets.typecode != "Q":
                self._offsets = array("Q", self._offsets)
            if len(self._offsets) > max(0, self.length):
                del self._offsets[max(0, self.length):]

    def __iter__(self) -> Iterator[LogEntry]:
        if self._offsets is None:
            self._offsets = array("Q")

        with open(self.path, "rb") as file_handle:
            index = 0
            while True:
                indexed = index < self.length and index == len(self._offsets)
                if indexed:
                    self._offsets.append(file_handle.tell())
                try:
                    log = cast(LogEntry, pickle.load(file_handle))
                except EOFError:
                    if indexed:
                        self._offsets.pop()
                    break
                yield log
                index += 1

    def iter_page(self, start: int, size: int) -> Generator[LogEntry, None, None]:
        if size <= 0:
            return

        start = max(0, start)
        if start >= self.length:
            return

        end = min(start + size, self.length)
        yield from self._iter_range(start, end)

    def _iter_range(self, start: int, end: int) -> Iterator[LogEntry]:
        if self._offsets is None:
            self._offsets = array("Q")
        offsets = self._offsets

        with open(self.path, "rb") as file_handle:
            if offsets and start < len(offsets):
                file_handle.seek(offsets[start])
                current_index = start
            elif offsets:
                file_handle.seek(offsets[-1])
                try:
                    pickle.load(file_handle)
                except EOFError:
                    return
                current_index = len(offsets)
            else:
                current_index = 0

            while current_index < end:
                appended_offset = current_index == len(offsets)
                if appended_offset:
                    offsets.append(file_handle.tell())
                try:
                    log = cast(LogEntry, pickle.load(file_handle))
                except EOFError:
                    if appended_offset:
                        offsets.pop()
                    break
                if current_index >= start:
                    yield log
                current_index += 1

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index: int) -> LogEntry:
        if index < 0:
            index += self.length
        if index < 0 or index >= self.length:
            raise IndexError(index)

        cache = self._index_cache
        if cache is not None and index in cache:
            log = cache.pop(index)
            cache[index] = log
            return log

        page = self.iter_page(index, 1)
        try:
            log = next(page)
        except StopIteration:
            raise IndexError(index) from None
        finally:
            page.close()
        self._remember(index, log)
        return log

    def __bool__(self) -> bool:
        return self.length > 0

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return list(self) == other
        return NotImplemented

    def __del__(self) -> None:
        if hasattr(self, "path"):
            self.release()

    def release(self) -> None:
        try:
            path = getattr(self, "path", "")
            if path and os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        if hasattr(self, "_index_cache"):
            self._index_cache = {}
        if hasattr(self, "_offsets"):
            self._offsets = None

    def _remember(self, index: int, log: LogEntry) -> None:
        if self._index_cache is None:
            self._index_cache = {}

        cache = self._index_cache
        if index in cache:
            del cache[index]
        cache[index] = log
        while len(cache) > _MAX_CACHED_ENTRIES:
            del cache[next(iter(cache))]
