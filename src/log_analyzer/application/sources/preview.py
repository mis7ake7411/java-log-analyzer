from __future__ import annotations

from dataclasses import dataclass
from os import stat_result
from pathlib import Path, PureWindowsPath

from log_analyzer.domain.log_types import OccurrenceCoordinate, SourceSnapshot
from log_analyzer.infrastructure.sources.context_index import SourceContextReader

_UNAVAILABLE_WARNING = "來源已變更、缺少或無法安全讀取，未顯示原始內容。"
_MISSING_LINE_WARNING = "所選行已不存在，未顯示原始內容。"
_ENCODING_NOTICE = "來源含無效 UTF-8 位元組；畫面以 � 取代顯示，無法還原原始字元。"


@dataclass(frozen=True, slots=True)
class SourcePreview:
    path_line: str
    lines: tuple[tuple[int, str], ...]
    match_line: int
    warning: str | None = None
    encoding_notice: str | None = None


def _warning_preview(filename: str, match_line: int, warning: str) -> SourcePreview:
    return SourcePreview(
        path_line=f"{filename}:{match_line}" if filename else str(match_line),
        lines=(),
        match_line=match_line,
        warning=warning,
    )


def _occurrence_position(occurrence: OccurrenceCoordinate) -> tuple[str, int]:
    try:
        filename = occurrence["filename"]
        match_line = occurrence["line_number"]
    except (KeyError, TypeError):
        return "", 0

    if not isinstance(filename, str) or not filename:
        return "", 0
    if not isinstance(match_line, int) or isinstance(match_line, bool) or match_line < 1:
        return filename, 0
    return filename, match_line


def _snapshot_values(snapshot: SourceSnapshot | None) -> tuple[int, int] | None:
    if snapshot is None:
        return None
    try:
        if snapshot.get("stable") is not True:
            return None
        snapshot_size = snapshot.get("size")
        snapshot_mtime_ns = snapshot.get("mtime_ns")
    except AttributeError:
        return None

    if (
        not isinstance(snapshot_size, int)
        or isinstance(snapshot_size, bool)
        or not isinstance(snapshot_mtime_ns, int)
        or isinstance(snapshot_mtime_ns, bool)
    ):
        return None
    return snapshot_size, snapshot_mtime_ns


def _resolve_source_path(input_path: str, filename: str) -> Path | None:
    normalized_filename = filename.replace("\\", "/")
    windows_filename = PureWindowsPath(filename)
    filename_parts = normalized_filename.split("/")
    if (
        normalized_filename.startswith("/")
        or windows_filename.drive
        or windows_filename.root
        or any(part == ".." for part in filename_parts)
    ):
        return None

    relative_parts = [part for part in filename_parts if part not in ("", ".")]
    if not relative_parts:
        return None
    try:
        root = Path(input_path).resolve(strict=True)
        if not root.is_dir():
            return None

        resolved_source = root.joinpath(*relative_parts).resolve(strict=True)
        resolved_source.relative_to(root)
        if not resolved_source.is_file():
            return None
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
    return resolved_source


def _matches_snapshot(source_stat: stat_result, snapshot_values: tuple[int, int]) -> bool:
    return (
        source_stat.st_size == snapshot_values[0]
        and source_stat.st_mtime_ns == snapshot_values[1]
    )


def _same_source_state(
    before: stat_result,
    after: stat_result,
    snapshot_values: tuple[int, int],
) -> bool:
    return (
        before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and _matches_snapshot(after, snapshot_values)
    )


def _read_context_and_stat_after(
    source: Path,
    source_stat: stat_result,
    first_line: int,
    last_line: int,
    reader: SourceContextReader,
) -> tuple[tuple[tuple[int, str], ...], bool, stat_result | None]:
    try:
        lines, has_encoding_error = reader.read_context_with_encoding(
            source,
            source_stat,
            first_line,
            last_line,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        reader.invalidate(source)
        try:
            source.stat()
        except (OSError, RuntimeError, ValueError, TypeError):
            pass
        return (), False, None

    try:
        return lines, has_encoding_error, source.stat()
    except (OSError, RuntimeError, ValueError, TypeError):
        reader.invalidate(source)
        return (), False, None


def _read_verified_context(
    resolved_source: Path,
    snapshot_values: tuple[int, int],
    match_line: int,
    radius: int,
    reader: SourceContextReader | None = None,
) -> tuple[tuple[tuple[int, str], ...], str | None, str | None]:
    context_reader = reader if reader is not None else SourceContextReader()
    try:
        before = resolved_source.stat()
        if not _matches_snapshot(before, snapshot_values):
            context_reader.invalidate(resolved_source)
            return (), _UNAVAILABLE_WARNING, None

        first_line = max(1, match_line - radius)
        last_line = match_line + radius
        lines, has_encoding_error, after = _read_context_and_stat_after(
            resolved_source,
            before,
            first_line,
            last_line,
            context_reader,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        context_reader.invalidate(resolved_source)
        return (), _UNAVAILABLE_WARNING, None

    if after is None:
        return (), _UNAVAILABLE_WARNING, None
    if not _same_source_state(before, after, snapshot_values):
        context_reader.invalidate(resolved_source)
        return (), _UNAVAILABLE_WARNING, None
    if not any(line_number == match_line for line_number, _ in lines):
        return (), _MISSING_LINE_WARNING, None
    encoding_notice = _ENCODING_NOTICE if has_encoding_error else None
    return tuple(lines), None, encoding_notice


def read_source_context(
    input_path: str,
    occurrence: OccurrenceCoordinate,
    snapshot: SourceSnapshot | None,
    radius: int = 3,
    *,
    reader: SourceContextReader | None = None,
) -> SourcePreview:
    """只在來源仍符合掃描快照且位於分析根目錄內時讀取命中行附近內容。"""
    filename, match_line = _occurrence_position(occurrence)
    snapshot_values = _snapshot_values(snapshot)
    if (
        not filename
        or match_line < 1
        or not isinstance(radius, int)
        or isinstance(radius, bool)
        or radius < 0
        or snapshot_values is None
    ):
        return _warning_preview(filename, match_line, _UNAVAILABLE_WARNING)

    resolved_source = _resolve_source_path(input_path, filename)
    if resolved_source is None:
        return _warning_preview(filename, match_line, _UNAVAILABLE_WARNING)

    lines, warning, encoding_notice = _read_verified_context(
        resolved_source,
        snapshot_values,
        match_line,
        radius,
        reader,
    )
    if warning is not None:
        return _warning_preview(filename, match_line, warning)

    return SourcePreview(
        path_line=f"{resolved_source}:{match_line}",
        lines=lines,
        match_line=match_line,
        warning=None,
        encoding_notice=encoding_notice,
    )
