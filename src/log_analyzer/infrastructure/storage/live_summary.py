"""將追蹤摘要的群組與出現座標寫入可分頁的暫存 SQLite 資料庫。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from log_analyzer.domain.parsing.aggregation import LEVEL_SORT_ORDER


class LiveSummaryStore:
    """以磁碟分組和排序追蹤事件，避免長期保留所有群組的 occurrences。

    讀取一筆群組時，會依既有明細契約將該群組的 occurrences 與 line_numbers
    還原成 list。因此單一群組很大時，該次讀取仍會使用與群組大小成正比的記憶體。
    寫入與 finalize 須在建立實例的執行緒執行。成功 finalize 會關閉建置連線；
    之後的唯讀分頁各自建立並關閉 SQLite 唯讀連線，因此可跨執行緒讀取或釋放。
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        """建立一個新的追蹤摘要暫存資料庫。

        Args:
            directory: 暫存資料夾；省略時建立並擁有專用暫存資料夾。

        Raises:
            OSError: 暫存路徑或 SQLite 資料庫無法建立時。
        """
        self._owns_directory = directory is None
        if directory is None:
            self._directory = Path(tempfile.mkdtemp(prefix="log-analyzer-summary-"))
        else:
            self._directory = Path(directory)
            self._directory.mkdir(parents=True, exist_ok=True)

        self._database_path: Path | None = None
        self._connection: sqlite3.Connection | None = None
        self._group_count = 0
        self._finalized = False
        self._released = False

        try:
            descriptor, database_name = tempfile.mkstemp(
                prefix="live-summary-",
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
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE live_summary_groups (
                    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_digest BLOB NOT NULL,
                    key_payload BLOB NOT NULL,
                    first_sequence INTEGER NOT NULL,
                    first_timestamp_sort BLOB NOT NULL,
                    level_sort_order INTEGER NOT NULL,
                    fields_payload BLOB NOT NULL,
                    last_timestamp_payload BLOB NOT NULL,
                    event_count INTEGER NOT NULL
                );
                CREATE INDEX idx_live_summary_group_digest
                    ON live_summary_groups (key_digest);
                CREATE TABLE live_summary_occurrences (
                    sequence INTEGER PRIMARY KEY,
                    group_id INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    FOREIGN KEY (group_id)
                        REFERENCES live_summary_groups (group_id)
                );
                CREATE INDEX idx_live_summary_occurrences_group
                    ON live_summary_occurrences (group_id, sequence);
                CREATE TABLE live_summary_order (
                    position INTEGER PRIMARY KEY,
                    group_id INTEGER NOT NULL UNIQUE,
                    FOREIGN KEY (group_id)
                        REFERENCES live_summary_groups (group_id)
                );
                """
            )
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as error:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error as close_error:
                    error.add_note(f"關閉初始化失敗的摘要資料庫時也發生錯誤：{close_error}")
            self._cleanup_files()
            raise OSError("無法建立追蹤摘要暫存資料庫") from error
        except BaseException:
            if connection is not None:
                connection.close()
            self._cleanup_files()
            raise

        self._connection = connection

    def add_event(self, event: dict[str, Any]) -> None:
        """將一筆完成事件加入同一分組交易。

        Args:
            event: 含 parser 欄位與 sequence、generation、raw_text 的事件。

        Raises:
            OSError: SQLite 寫入失敗；原始 SQLite 例外保留在 __cause__。
            RuntimeError: 儲存器已釋放或已完成排序。
        """
        connection = self._require_writable()
        level = event["level"]
        logger = event["logger"]
        message = event["message"]
        message_body = event.get("message_body", "")
        stacktrace = "".join(event.get("stacktrace_lines", ())).strip()
        timestamp = event["timestamp"]
        sequence = int(event["sequence"])
        generation = int(event["generation"])
        line_number = (
            event["line_num"] if "line_num" in event else event["line_number"]
        )
        raw_text = event.get("raw_text")
        if raw_text is None:
            raw_text = event.get("full_text", "")

        key_payload = _encode_json(
            (level, logger, message, message_body, stacktrace)
        )
        key_digest = hashlib.sha256(key_payload).digest()
        group_fields = {
            "timestamp": timestamp,
            "level": level,
            "thread": event["thread"],
            "logger": logger,
            "filename": event["filename"],
            "message": message,
            "message_body": message_body,
            "stacktrace": stacktrace,
        }
        occurrence = {
            "filename": event["filename"],
            "line_number": line_number,
            "timestamp": timestamp,
            "thread": event["thread"],
            "mdc": dict(event.get("mdc") or {}),
            "sequence": sequence,
            "generation": generation,
            "raw_text": raw_text,
        }

        try:
            group_id = self._find_group(key_digest, key_payload)
            is_new_group = group_id is None
            if is_new_group:
                cursor = connection.execute(
                    """
                    INSERT INTO live_summary_groups (
                        key_digest,
                        key_payload,
                        first_sequence,
                        first_timestamp_sort,
                        level_sort_order,
                        fields_payload,
                        last_timestamp_payload,
                        event_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        key_digest,
                        key_payload,
                        sequence,
                        _timestamp_sort_key(timestamp),
                        LEVEL_SORT_ORDER.get(level, 99),
                        _encode_json(group_fields),
                        _encode_json(timestamp),
                    ),
                )
                group_id = int(cursor.lastrowid)
            else:
                connection.execute(
                    """
                    UPDATE live_summary_groups
                    SET event_count = event_count + 1,
                        last_timestamp_payload = ?
                    WHERE group_id = ?
                    """,
                    (_encode_json(timestamp), group_id),
                )

            connection.execute(
                """
                INSERT INTO live_summary_occurrences (sequence, group_id, payload)
                VALUES (?, ?, ?)
                """,
                (sequence, group_id, _encode_json(occurrence)),
            )
        except sqlite3.Error as error:
            raise OSError("無法寫入追蹤摘要事件") from error

        if is_new_group:
            self._group_count += 1

    def finalize(self, sort_by: str) -> None:
        """依批次分析的時間或等級規則建立固定順序索引並提交資料。

        Args:
            sort_by: ``level`` 使用等級與時間排序；其他值依時間排序。

        Raises:
            OSError: SQLite 排序索引或交易提交失敗。
            RuntimeError: 儲存器已釋放或已完成排序。
        """
        connection = self._require_writable()
        if sort_by == "level":
            ordering = (
                "level_sort_order, first_timestamp_sort, first_sequence"
            )
        else:
            ordering = "first_timestamp_sort, first_sequence"

        try:
            connection.execute(
                f"""
                INSERT INTO live_summary_order (position, group_id)
                SELECT ROW_NUMBER() OVER (ORDER BY {ordering}) - 1, group_id
                FROM live_summary_groups
                """
            )
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error as rollback_error:
                    raise OSError("追蹤摘要排序失敗且無法復原") from rollback_error
            raise OSError("無法完成追蹤摘要排序") from error

        try:
            connection.close()
        except sqlite3.Error as error:
            raise OSError("無法關閉追蹤摘要建置連線") from error

        self._connection = None
        self._finalized = True

    def __len__(self) -> int:
        """回傳已分組的群組數。"""
        return self._group_count

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """依固定順序逐組讀取摘要明細。"""
        return self.iter_page(0, len(self))

    def iter_page(self, start: int, size: int) -> Iterator[dict[str, Any]]:
        """讀取從零起算的一頁群組，不載入其他群組。

        Args:
            start: 從零起算的群組位置；負數視為 0。
            size: 最多回傳的群組數；小於等於 0 時回傳空迭代器。

        Returns:
            Iterator[dict[str, Any]]: 可逐組讀取的追蹤摘要明細。

        Raises:
            OSError: SQLite 讀取失敗；原始 SQLite 例外保留在 __cause__。
            RuntimeError: 儲存器尚未排序或已釋放。
        """
        database_uri = self._readonly_database_uri()
        start = max(0, start)
        if size <= 0:
            return iter(())

        def generate() -> Iterator[dict[str, Any]]:
            connection: sqlite3.Connection | None = None
            cursor: sqlite3.Cursor | None = None
            try:
                connection = sqlite3.connect(
                    database_uri,
                    uri=True,
                    isolation_level=None,
                )
                cursor = connection.execute(
                    """
                    SELECT group_id
                    FROM live_summary_order
                    WHERE position >= ?
                    ORDER BY position
                    LIMIT ?
                    """,
                    (start, size),
                )
                while True:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    yield self._read_group(connection, int(row[0]))
            except sqlite3.Error as error:
                raise OSError("無法讀取追蹤摘要群組") from error
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
                    raise OSError("無法關閉追蹤摘要讀取連線") from close_error

        return generate()

    def __getitem__(self, index: int) -> dict[str, Any]:
        """以整數索引讀取一組明細，支援負索引。"""
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)

        page = self.iter_page(index, 1)
        try:
            return next(page)
        except StopIteration:
            raise IndexError(index) from None
        finally:
            close = getattr(page, "close", None)
            if callable(close):
                close()

    def release(self) -> None:
        """關閉資料庫並刪除此儲存器建立的資料庫檔案；重複呼叫安全。"""
        if self._released:
            return

        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as error:
                raise OSError("無法關閉追蹤摘要資料庫") from error
            self._connection = None

        self._cleanup_files()
        self._released = True

    def _find_group(self, digest: bytes, payload: bytes) -> int | None:
        connection = self._require_writable()
        cursor = connection.execute(
            "SELECT group_id, key_payload FROM live_summary_groups WHERE key_digest = ?",
            (digest,),
        )
        try:
            for group_id, existing_payload in cursor:
                if bytes(existing_payload) == payload:
                    return int(group_id)
        finally:
            cursor.close()
        return None

    def _read_group(
        self, connection: sqlite3.Connection, group_id: int
    ) -> dict[str, Any]:
        group_cursor: sqlite3.Cursor | None = None
        occurrence_cursor: sqlite3.Cursor | None = None
        try:
            group_cursor = connection.execute(
                """
                SELECT fields_payload, last_timestamp_payload, event_count
                FROM live_summary_groups
                WHERE group_id = ?
                """,
                (group_id,),
            )
            row = group_cursor.fetchone()
            if row is None:
                raise OSError(f"追蹤摘要缺少群組 {group_id}")

            fields_payload, last_timestamp_payload, event_count = row
            group = _decode_json(fields_payload)
            group["last_timestamp"] = _decode_json(last_timestamp_payload)
            group["count"] = int(event_count)
            group["line_numbers"] = []
            group["occurrences"] = []

            occurrence_cursor = connection.execute(
                """
                SELECT payload
                FROM live_summary_occurrences
                WHERE group_id = ?
                ORDER BY sequence
                """,
                (group_id,),
            )
            while True:
                occurrence_row = occurrence_cursor.fetchone()
                if occurrence_row is None:
                    break
                occurrence = _decode_json(occurrence_row[0])
                group["line_numbers"].append(occurrence["line_number"])
                group["occurrences"].append(occurrence)
            return group
        except sqlite3.Error as error:
            raise OSError("無法讀取追蹤摘要出現座標") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise OSError("追蹤摘要暫存內容無法解碼") from error
        finally:
            if occurrence_cursor is not None:
                occurrence_cursor.close()
            if group_cursor is not None:
                group_cursor.close()

    def _require_writable(self) -> sqlite3.Connection:
        if self._released or self._finalized:
            raise RuntimeError("live summary store is not writable")
        if self._connection is None:
            raise RuntimeError("live summary database is closed")
        return self._connection

    def _require_readable(self) -> Path:
        if self._released:
            raise RuntimeError("live summary store has been released")
        if not self._finalized:
            raise RuntimeError("live summary store has not been finalized")
        if self._database_path is None:
            raise RuntimeError("live summary database is closed")
        return self._database_path

    def _readonly_database_uri(self) -> str:
        database_path = self._require_readable()
        return f"{database_path.resolve().as_uri()}?mode=ro"

    def _cleanup_files(self) -> None:
        if self._database_path is not None:
            try:
                self._database_path.unlink()
            except FileNotFoundError:
                pass
        if self._owns_directory:
            try:
                self._directory.rmdir()
            except FileNotFoundError:
                pass


def _encode_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _decode_json(payload: bytes | memoryview) -> Any:
    return json.loads(bytes(payload).decode("ascii"))


def _timestamp_sort_key(timestamp: str) -> bytes:
    return timestamp.encode("utf-8", errors="surrogatepass")
