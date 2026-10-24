from __future__ import annotations

from dataclasses import dataclass

from log_analyzer.domain.log_sources import LogSourceSelection, select_log_source


@dataclass
class SourceFileSelection:
    path: str = ""
    selected_files: tuple[str, ...] | None = None
    available: LogSourceSelection | None = None

    def resolve(self, path: str) -> LogSourceSelection:
        source = select_log_source(path)
        if source.path != self.path:
            self.path = source.path
            self.selected_files = None
        self.available = source
        if self.selected_files is not None:
            return select_log_source(source.path, selected_files=self.selected_files)
        return source

    def choose(self, source: LogSourceSelection, filenames: tuple[str, ...]) -> None:
        if not set(filenames).issubset(source.filenames):
            raise ValueError("只能選擇清單中的日誌檔案")
        selected = tuple(dict.fromkeys(filenames))
        validated = select_log_source(source.path, selected_files=selected)
        self.path = validated.path
        self.selected_files = validated.filenames
        self.available = source
