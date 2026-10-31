from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, TypedDict, runtime_checkable


@dataclass
class FileScanCoverage:
    filename: str
    total_lines: int = 0
    parsed_entries: int = 0
    blank_lines: int = 0
    stacktrace_lines: int = 0
    continuation_lines: int = 0
    unrecognized_lines: int = 0
    unrecognized_samples: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class ScanDiagnostics:
    files_scanned: int = 0
    parsed_entries: int = 0
    after_time: int = 0
    after_level: int = 0
    after_keyword: int = 0
    encoding_error_files: int = 0
    encoding_error_lines: int = 0
    file_coverage: list[FileScanCoverage] = field(default_factory=list)


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
    mdc: dict[str, str]


class _OccurrenceCoordinateRequired(TypedDict):
    filename: str
    line_number: int
    timestamp: str
    thread: str


class OccurrenceCoordinate(_OccurrenceCoordinateRequired, total=False):
    mdc: dict[str, str]


class SourceSnapshot(TypedDict):
    size: int
    mtime_ns: int
    stable: bool


@runtime_checkable
class MatchedLogs(Protocol):
    def __iter__(self) -> Iterator[LogEntry]: ...

    def __len__(self) -> int: ...
