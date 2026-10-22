from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, TypedDict, runtime_checkable


@dataclass
class ScanDiagnostics:
    files_scanned: int = 0
    parsed_entries: int = 0
    after_time: int = 0
    after_level: int = 0
    after_keyword: int = 0


class LogEntry(TypedDict, total=False):
    timestamp: str
    thread: str
    level: str
    logger: str
    message: str
    message_body: str
    full_text: str
    filename: str
    line_num: int
    count: int
    stacktrace: str
    stacktrace_lines: list[str]


class OccurrenceCoordinate(TypedDict):
    filename: str
    line_number: int
    timestamp: str
    thread: str


class SourceSnapshot(TypedDict):
    size: int
    mtime_ns: int
    stable: bool


@runtime_checkable
class MatchedLogs(Protocol):
    def __iter__(self) -> Iterator[LogEntry]: ...

    def __len__(self) -> int: ...
