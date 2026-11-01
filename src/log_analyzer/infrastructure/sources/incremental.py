"""Bounded binary reads that preserve source generations across rotation."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


_ANCHOR_BYTES = 64
_MAX_PENDING_GENERATIONS = 16


@dataclass(frozen=True, slots=True)
class ReadBatch:
    data: bytes
    generation: int
    reset: bool
    gap: str | None
    missing: bool


class IncrementalFileReader:
    """Read appended bytes from one path while retaining replaced file handles."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file: BinaryIO | None = None
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._generation = 0
        self._candidates: deque[tuple[BinaryIO, tuple[int, int]]] = deque()
        self._candidate_identities: set[tuple[int, int]] = set()
        self._prefix_anchor = b""
        self._tail_anchor = b""
        self._tail_anchor_offset = 0
        self._closed = False

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def backlog(self) -> int:
        if self._file is None:
            return 0
        size = os.fstat(self._file.fileno()).st_size
        return max(0, size - self._offset)

    def read(self, max_bytes: int = 65536) -> ReadBatch:
        if self._closed:
            raise ValueError("reader is closed")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")

        path_identity = self._path_identity()
        missing = path_identity is None

        if self._file is None:
            if missing:
                return self._batch(b"", missing=True)
            try:
                self._activate(self._open_source(), generation=self._generation)
            except FileNotFoundError:
                return self._batch(b"", missing=True)
            path_identity = self._identity

        if (
            not missing
            and path_identity != self._identity
            and path_identity not in self._candidate_identities
        ):
            if len(self._candidates) >= _MAX_PENDING_GENERATIONS:
                raise OSError(
                    f"reader limit reached: {_MAX_PENDING_GENERATIONS} pending "
                    "source generations; refusing to drop unread data"
                )
            try:
                self._stage_candidate()
            except FileNotFoundError:
                missing = True

        gap = self._verify_active_content()
        if gap is not None:
            self._start_generation()
            return self._batch(b"", reset=True, gap=gap, missing=missing)

        data = self._read_active(max_bytes)
        if data:
            return self._batch(data, missing=missing)

        if self._candidates:
            old_file = self._file
            candidate, identity = self._candidates.popleft()
            self._candidate_identities.discard(identity)
            if old_file is not None:
                old_file.close()
            self._activate(
                candidate, generation=self._generation + 1, identity=identity
            )
            return self._batch(b"", reset=True, missing=missing)

        return self._batch(b"", missing=missing)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._file is not None:
            self._file.close()
            self._file = None
        while self._candidates:
            candidate, _identity = self._candidates.popleft()
            candidate.close()
        self._candidate_identities.clear()

    def _batch(
        self,
        data: bytes,
        *,
        reset: bool = False,
        gap: str | None = None,
        missing: bool = False,
    ) -> ReadBatch:
        return ReadBatch(
            data=data,
            generation=self._generation,
            reset=reset,
            gap=gap,
            missing=missing,
        )

    def _path_identity(self) -> tuple[int, int] | None:
        try:
            stat_result = self.path.stat()
        except FileNotFoundError:
            return None
        return stat_result.st_dev, stat_result.st_ino

    def _open_source(self) -> BinaryIO:
        if os.name != "nt":
            return self.path.open("rb", buffering=0)

        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = create_file(
            str(self.path),
            0x80000000,  # GENERIC_READ
            0x00000001 | 0x00000002 | 0x00000004,  # share read, write, and delete
            None,
            3,  # OPEN_EXISTING
            0x00000080,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close_handle(handle)
            raise
        try:
            return os.fdopen(descriptor, "rb", buffering=0)
        except BaseException:
            os.close(descriptor)
            raise

    def _stage_candidate(self) -> None:
        assert self._identity is not None
        candidate = self._open_source()
        identity = self._file_identity(candidate)
        if identity == self._identity or identity in self._candidate_identities:
            candidate.close()
            return
        self._candidates.append((candidate, identity))
        self._candidate_identities.add(identity)

    @staticmethod
    def _file_identity(file_handle: BinaryIO) -> tuple[int, int]:
        stat_result = os.fstat(file_handle.fileno())
        return stat_result.st_dev, stat_result.st_ino

    def _activate(
        self,
        file_handle: BinaryIO,
        *,
        generation: int,
        identity: tuple[int, int] | None = None,
    ) -> None:
        self._file = file_handle
        self._identity = identity or self._file_identity(file_handle)
        self._generation = generation
        self._offset = 0
        self._clear_anchors()

    def _verify_active_content(self) -> str | None:
        assert self._file is not None
        size = os.fstat(self._file.fileno()).st_size
        if size < self._offset:
            return "Possible copytruncate: source shrank before unread bytes were consumed."

        if self._prefix_anchor:
            self._file.seek(0)
            if self._file.read(len(self._prefix_anchor)) != self._prefix_anchor:
                return "Possible copytruncate: source prefix changed; unread bytes may be lost."

        if self._tail_anchor:
            self._file.seek(self._tail_anchor_offset)
            if self._file.read(len(self._tail_anchor)) != self._tail_anchor:
                return "Possible copytruncate: source tail changed; unread bytes may be lost."

        self._file.seek(self._offset)
        return None

    def _start_generation(self) -> None:
        self._generation += 1
        self._offset = 0
        self._clear_anchors()
        assert self._file is not None
        self._file.seek(0)

    def _read_active(self, max_bytes: int) -> bytes:
        assert self._file is not None
        self._file.seek(self._offset)
        data = self._file.read(max_bytes)
        if not data:
            return b""

        start_offset = self._offset
        self._offset += len(data)
        if len(self._prefix_anchor) < _ANCHOR_BYTES and start_offset == len(
            self._prefix_anchor
        ):
            anchor_length = min(_ANCHOR_BYTES - len(self._prefix_anchor), len(data))
            self._prefix_anchor += data[:anchor_length]
        tail_length = min(_ANCHOR_BYTES, len(data))
        self._tail_anchor = data[-tail_length:]
        self._tail_anchor_offset = self._offset - tail_length
        return data

    def _clear_anchors(self) -> None:
        self._prefix_anchor = b""
        self._tail_anchor = b""
        self._tail_anchor_offset = 0
