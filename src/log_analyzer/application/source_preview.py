from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..domain.log_types import OccurrenceCoordinate, SourceSnapshot

_UNAVAILABLE_WARNING = "來源已變更、缺少或無法安全讀取，未顯示原始內容。"
_MISSING_LINE_WARNING = "所選行已不存在，未顯示原始內容。"


@dataclass(frozen=True, slots=True)
class SourcePreview:
    path_line: str
    lines: tuple[tuple[int, str], ...]
    match_line: int
    warning: str | None = None


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


def _read_verified_context(
    resolved_source: Path,
    snapshot_size: int,
    snapshot_mtime_ns: int,
    match_line: int,
    radius: int,
) -> tuple[tuple[tuple[int, str], ...], str | None]:
    try:
        before = resolved_source.stat()
        if before.st_size != snapshot_size or before.st_mtime_ns != snapshot_mtime_ns:
            return (), _UNAVAILABLE_WARNING

        first_line = max(1, match_line - radius)
        last_line = match_line + radius
        lines: list[tuple[int, str]] = []
        with resolved_source.open("r", encoding="utf-8", errors="ignore") as file_handle:
            for line_number, text in enumerate(file_handle, start=1):
                if line_number > last_line:
                    break
                if line_number >= first_line:
                    lines.append((line_number, text.rstrip("\r\n")))

        after = resolved_source.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or after.st_size != snapshot_size
            or after.st_mtime_ns != snapshot_mtime_ns
        ):
            return (), _UNAVAILABLE_WARNING
        if not any(line_number == match_line for line_number, _ in lines):
            return (), _MISSING_LINE_WARNING
    except (OSError, RuntimeError, ValueError, TypeError):
        return (), _UNAVAILABLE_WARNING
    return tuple(lines), None


def read_source_context(
    input_path: str,
    occurrence: OccurrenceCoordinate,
    snapshot: SourceSnapshot | None,
    radius: int = 3,
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

    snapshot_size, snapshot_mtime_ns = snapshot_values
    lines, warning = _read_verified_context(
        resolved_source,
        snapshot_size,
        snapshot_mtime_ns,
        match_line,
        radius,
    )
    if warning is not None:
        return _warning_preview(filename, match_line, warning)

    return SourcePreview(
        path_line=f"{resolved_source}:{match_line}",
        lines=lines,
        match_line=match_line,
        warning=None,
    )
