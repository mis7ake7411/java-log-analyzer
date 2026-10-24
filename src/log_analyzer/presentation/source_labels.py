from __future__ import annotations
from pathlib import Path
from log_analyzer.domain.log_sources import LogSourceSelection


def source_file_sizes(source: LogSourceSelection) -> dict[str, int | None]:
    sizes = {}
    for name in source.filenames:
        try:
            sizes[name] = (Path(source.root) / name).stat().st_size
        except OSError:
            sizes[name] = None
    return sizes


def format_size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable size unit")
