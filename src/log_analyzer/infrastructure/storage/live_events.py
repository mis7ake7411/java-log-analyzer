"""將即時分析完成的事件保留在有容量上限的暫存 SQLite 儲存中。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any


_SNAPSHOT_READ_BATCH_SIZE = 100


class LiveStorageLimitError(OSError):
    """即時事件的 JSON payload 已超過設定容量。"""


class LiveEventStore:
    """依序保存完成事件，並以固定截止序號建立可分頁快照。

    容量只計算每筆事件的 ASCII JSON payload bytes，不包含 SQLite 結構、
    索引、journal 或檔案系統配置等額外磁碟用量。寫入、直接分頁與生命週期
    操作須由建立儲存器的同一執行緒呼叫；快照讀取使用獨立的短期唯讀連線，
    可交給背景執行緒。有效快照保留資料庫檔案，關閉儲存器後仍可讀取。
    """

    def __init__(
        self,
        directory: str | Path | None = None,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        """建立新的事件資料庫。

        Args:
            directory: 暫存資料夾；省略時建立並擁有專用暫存資料夾。
            max_bytes: JSON payload bytes 的總容量上限。

        Raises:
            OSError: 暫存路徑或 SQLite 資料庫無法建立時。
            ValueError: 容量上限為負數。
            TypeError: 容量上限不是整數。
        """
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
            raise TypeError("max_bytes must be an integer")
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")

        self.max_bytes = max_bytes
        self._owns_directory = directory is None
        if directory is None:
            self._directory = Path(tempfile.mkdtemp(prefix="log-analyzer-live-"))
        else:
            self._directory = Path(directory)
            self._directory.mkdir(parents=True, exist_ok=True)

        self._database_path: Path | None = None
        self._connection: sqlite3.Connection | None = None
        self._count = 0
        self._used_bytes = 0
        self._snapshot_count = 0
        self._closed = False

        try:
            descriptor, database_name = tempfile.mkstemp(
                prefix="live-events-",
                suffix=".sqlite3",
                dir=self._directory,
            )
            self._database_path = Path(database_name)
            os.close(descriptor)
        except BaseException:
            self._cleanup_files()
            raise

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                isolation_level=None,
            )
            connection.execute(
                """
                CREATE TABLE live_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation INTEGER NOT NULL,
                    payload BLOB NOT NULL
                )
                """
            )
        except sqlite3.Error as error:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error as close_error:
                    error.add_note(f"Closing the failed database also failed: {close_error}")
            self._cleanup_files()
            raise OSError("無法建立即時事件資料庫") from error
        except BaseException:
            if connection is not None:
                connection.close()
            self._cleanup_files()
            raise

        self._connection = connection

    def append(self, entry: dict[str, Any], generation: int) -> int:
        """追加一筆已完成事件並回傳從 1 起算的序號。

        Args:
            entry: 已完成事件；內容會在呼叫時序列化，後續修改不影響儲存結果。
            generation: 事件所屬的來源世代。

        Returns:
            int: 此事件在工作階段中的唯一序號。

        Raises:
            LiveStorageLimitError: JSON payload bytes 超過容量上限。
            OSError: SQLite 寫入失敗；原始 SQLite 例外保留在 __cause__。
            RuntimeError: 儲存器已關閉。
            TypeError: 事件無法序列化為 JSON。
            ValueError: 事件包含非有限 JSON 數值。
        """
        self._ensure_open()
        payload = json.dumps(
            entry,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        payload_bytes = len(payload)
        if self._used_bytes + payload_bytes > self.max_bytes:
            raise LiveStorageLimitError(
                f"即時事件 JSON payload 超過 {self.max_bytes} bytes 容量上限"
            )

        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "INSERT INTO live_events (generation, payload) VALUES (?, ?)",
                    (generation, payload),
                )
                sequence = int(cursor.lastrowid)
                connection.execute("COMMIT")
            except BaseException as error:
                if connection.in_transaction:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error as rollback_error:
                        raise OSError("即時事件交易失敗且無法復原") from rollback_error
                if isinstance(error, sqlite3.Error):
                    raise OSError("無法寫入即時事件") from error
                raise
        except OSError:
            raise
        except sqlite3.Error as error:
            raise OSError("無法開始即時事件寫入交易") from error

        self._count += 1
        self._used_bytes += payload_bytes
        return sequence

    def __len__(self) -> int:
        """回傳已提交的事件數，不掃描資料列。"""
        return self._count

    def iter_page(
        self,
        start: int,
        size: int,
        cutoff: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """以 0 起算的位置讀取一頁，可限制在指定的事件序號截止點。

        Args:
            start: 0 起算的頁面位置；負數視為 0。
            size: 最多讀取的筆數；小於等於 0 時回傳空迭代器。
            cutoff: 最大包含序號；省略時固定為呼叫當下的最新序號。

        Returns:
            Iterator[dict[str, Any]]: 最多 ``size`` 筆的事件資料。

        Raises:
            OSError: SQLite 讀取失敗；原始 SQLite 例外保留在 __cause__。
            RuntimeError: 儲存器已關閉。
        """
        self._ensure_open()
        effective_cutoff = self._count if cutoff is None else min(cutoff, self._count)
        return self._iter_rows(start, size, effective_cutoff)

    def snapshot(self) -> LiveEventSnapshot:
        """建立截止於目前最新序號的固定快照。"""
        self._ensure_open()
        self._snapshot_count += 1
        return LiveEventSnapshot(self, self._count)

    def close(self) -> None:
        """停止接收事件；活動中的快照仍可讀取，直到它們全部釋放。"""
        self._closed = True
        self._finalize_if_released()

    def _iter_rows(
        self,
        start: int,
        size: int,
        cutoff: int,
    ) -> Iterator[dict[str, Any]]:
        start = max(0, start)
        if size <= 0 or cutoff <= 0 or start >= cutoff:
            return iter(())

        connection = self._require_connection()

        def generate() -> Iterator[dict[str, Any]]:
            cursor: sqlite3.Cursor | None = None
            try:
                cursor = connection.execute(
                    """
                    SELECT sequence, generation, payload
                    FROM live_events
                    WHERE sequence > ? AND sequence <= ?
                    ORDER BY sequence
                    LIMIT ?
                    """,
                    (start, cutoff, size),
                )
                while True:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    sequence, generation, payload = row
                    event = json.loads(payload.decode("ascii"))
                    event["sequence"] = int(sequence)
                    event["generation"] = int(generation)
                    yield event
            except sqlite3.Error as error:
                raise OSError("無法讀取即時事件") from error
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except sqlite3.Error as error:
                        raise OSError("無法關閉即時事件讀取游標") from error

        return generate()

    def _iter_snapshot_rows(
        self,
        start: int,
        size: int,
        cutoff: int,
    ) -> Iterator[dict[str, Any]]:
        start = max(0, start)
        if size <= 0 or cutoff <= 0 or start >= cutoff:
            return iter(())

        def generate() -> Iterator[dict[str, Any]]:
            last_sequence = start
            remaining = size
            while remaining > 0 and last_sequence < cutoff:
                connection: sqlite3.Connection | None = None
                cursor: sqlite3.Cursor | None = None
                rows: list[tuple[int, int, bytes]] = []
                try:
                    connection = self._open_readonly_connection()
                    cursor = connection.execute(
                        """
                        SELECT sequence, generation, payload
                        FROM live_events
                        WHERE sequence > ? AND sequence <= ?
                        ORDER BY sequence
                        LIMIT ?
                        """,
                        (
                            last_sequence,
                            cutoff,
                            min(remaining, _SNAPSHOT_READ_BATCH_SIZE),
                        ),
                    )
                    rows = cursor.fetchall()
                except sqlite3.Error as error:
                    raise OSError("無法讀取即時事件快照") from error
                finally:
                    close_error: sqlite3.Error | None = None
                    if cursor is not None:
                        try:
                            cursor.close()
                        except sqlite3.Error as error:
                            close_error = error
                    if connection is not None:
                        try:
                            connection.close()
                        except sqlite3.Error as error:
                            close_error = close_error or error
                    if close_error is not None:
                        raise OSError("無法關閉即時事件唯讀連線") from close_error

                if not rows:
                    break

                for sequence, generation, payload in rows:
                    last_sequence = int(sequence)
                    remaining -= 1
                    event = json.loads(payload.decode("ascii"))
                    event["sequence"] = last_sequence
                    event["generation"] = int(generation)
                    yield event

        return generate()

    def _open_readonly_connection(self) -> sqlite3.Connection:
        if self._database_path is None:
            raise RuntimeError("live event database is closed")
        database_uri = f"{self._database_path.resolve().as_uri()}?mode=ro"
        try:
            return sqlite3.connect(
                database_uri,
                uri=True,
                isolation_level=None,
            )
        except sqlite3.Error as error:
            raise OSError("無法開啟即時事件唯讀連線") from error

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("live event store is closed")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("live event database is closed")
        return self._connection

    def _snapshot_released(self) -> None:
        self._snapshot_count -= 1
        self._finalize_if_released()

    def _finalize_if_released(self) -> None:
        if not self._closed or self._snapshot_count:
            return

        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as error:
                raise OSError("無法關閉即時事件資料庫") from error
            self._connection = None

        self._cleanup_files()

    def _cleanup_files(self) -> None:
        if self._database_path is not None:
            try:
                self._database_path.unlink()
            except FileNotFoundError:
                pass
        self._remove_owned_directory_if_empty()

    def _remove_owned_directory_if_empty(self) -> None:
        if not self._owns_directory:
            return
        try:
            self._directory.rmdir()
        except FileNotFoundError:
            pass


class LiveEventSnapshot:
    """保留固定事件截止序號的可分頁快照。"""

    __slots__ = ("_cutoff", "_released", "_store")

    def __init__(self, store: LiveEventStore, cutoff: int) -> None:
        self._store = store
        self._cutoff = cutoff
        self._released = False

    @property
    def cutoff(self) -> int:
        """快照包含的最大事件序號。"""
        return self._cutoff

    def __len__(self) -> int:
        """回傳此快照中的事件數。"""
        return self._cutoff

    def iter_page(self, start: int, size: int) -> Iterator[dict[str, Any]]:
        """讀取快照範圍內的一頁事件。"""
        self._ensure_active()
        return self._store._iter_snapshot_rows(start, size, self._cutoff)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """依序遍歷快照包含的所有事件。"""
        return self.iter_page(0, self._cutoff)

    def release(self) -> None:
        """釋放快照對資料庫的保留；重複呼叫不會改變狀態。"""
        if self._released:
            return
        self._released = True
        self._store._snapshot_released()

    def _ensure_active(self) -> None:
        if self._released:
            raise RuntimeError("live event snapshot has been released")
