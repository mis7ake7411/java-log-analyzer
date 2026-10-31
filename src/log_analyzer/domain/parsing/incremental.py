"""Incrementally assemble complete log entries from byte chunks."""

from __future__ import annotations

import codecs
import copy
import io
from datetime import datetime
from typing import cast

from log_analyzer.domain.log_types import LogEntry
from log_analyzer.domain.logback.pattern import (
    DEFAULT_LOGBACK_REGEX,
    compile_logback_pattern,
)
from log_analyzer.domain.parsing.entries import _EntryAccumulator
from log_analyzer.domain.text_decoding import (
    SOURCE_TEXT_ENCODING,
    SOURCE_TEXT_ERRORS,
    normalize_decoded_line,
)
from log_analyzer.domain.timezone import to_local_timezone


class IncrementalEntryParser:
    """以可持續 feed 的方式解析來源 bytes，並明確區分 EOF 與事件完成。"""

    def __init__(
        self,
        filename: str,
        log_pattern: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        decoder = codecs.getincrementaldecoder(SOURCE_TEXT_ENCODING)(
            errors=SOURCE_TEXT_ERRORS
        )
        self._decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
        self._entry_pattern = (
            compile_logback_pattern(log_pattern)
            if log_pattern
            else DEFAULT_LOGBACK_REGEX
        )
        self._accumulator = _EntryAccumulator(
            filename,
            to_local_timezone(start_time) if start_time is not None else None,
            to_local_timezone(end_time) if end_time is not None else None,
        )
        self._line_buffer = ""
        self._line_number = 0
        self._finished = False
        self._pending_cr = False

    @property
    def pending(self) -> LogEntry | None:
        """回傳目前未完成事件的隔離副本。"""
        entry = self._accumulator.current_entry
        return None if entry is None else copy.deepcopy(entry)

    @property
    def line_number(self) -> int:
        """回傳已辨識的實體行數。"""
        return self._line_number

    @property
    def has_pending_input(self) -> bool:
        """包含未完成事件、半行或解碼器內尚未組成字元的 bytes。"""
        return bool(
            self._accumulator.current_entry is not None
            or self._line_buffer
            or self._decoder.getstate()[0]
            or self._pending_cr
        )

    def feed(self, data: bytes) -> list[LogEntry]:
        """消費一段 bytes，只交出被下一個事件首行結束的事件。"""
        if self._finished:
            raise RuntimeError("cannot feed bytes after finish")
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")

        if data:
            self._pending_cr = data.endswith(b"\r")
        decoded = self._decoder.decode(data, final=False)
        return self._consume_decoded(decoded)

    def finish(self) -> list[LogEntry]:
        """明確結束輸入，完成尾端部分行與目前事件；重複呼叫不再交出事件。"""
        if self._finished:
            return []
        self._finished = True
        self._pending_cr = False

        emitted = self._consume_decoded(self._decoder.decode(b"", final=True))
        if self._line_buffer:
            emitted.extend(self._consume_line(self._line_buffer))
            self._line_buffer = ""

        completed = self._accumulator.finish()
        if completed is not None:
            emitted.append(completed)
        self._accumulator.current_entry = None
        return emitted

    def _consume_decoded(self, decoded: str) -> list[LogEntry]:
        self._line_buffer += decoded
        emitted: list[LogEntry] = []
        while True:
            newline_index = self._line_buffer.find("\n")
            if newline_index < 0:
                break
            line = self._line_buffer[: newline_index + 1]
            self._line_buffer = self._line_buffer[newline_index + 1 :]
            emitted.extend(self._consume_line(line))
        return emitted

    def _consume_line(self, line: str) -> list[LogEntry]:
        self._line_number += 1
        line, _has_encoding_error = normalize_decoded_line(line)
        match = self._entry_pattern.match(line)
        if match is not None:
            completed = self._accumulator.start_entry(match, self._line_number)
            current = self._accumulator.current_entry
            if current is not None:
                cast(dict[str, object], current)["raw_text"] = line
            return [] if completed is None else [completed]

        current = self._accumulator.current_entry
        self._accumulator.append_continuation(self._line_number, line)
        if current is not None:
            raw_text = cast(dict[str, object], current).get("raw_text", "")
            cast(dict[str, object], current)["raw_text"] = f"{raw_text}{line}"
        return []
