"""替磁碟暫存結果提供迭代、索引與分頁介面，避免畫面一次載入所有群組。

parser._persist_matched_logs 建立此物件並交出暫存檔所有權；介面放棄結果時
呼叫 release。檔案保存的是本程式建立的 pickle，不是一般文字日誌。
"""

from __future__ import annotations

import os
import pickle
from array import array
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from typing import cast

from .log_types import LogEntry, ScanDiagnostics, SourceSnapshot

_MAX_CACHED_ENTRIES = 64


@dataclass(slots=True)
class MatchedLogsStore:
    """可重複讀取的群組儲存器；path 是暫存檔，length 是群組數。

    _offsets 保存各筆資料的位元組位置，與原始日誌的行號不同；
    _index_cache 只快取少量索引讀取結果，source_states 則供原文定位核對。
    """
    path: str
    length: int
    _index_cache: dict[int, LogEntry] | None = None
    source_states: dict[str, SourceSnapshot] = field(default_factory=dict)
    diagnostics: ScanDiagnostics = field(default_factory=ScanDiagnostics)
    _offsets: array | None = None

    def __post_init__(self) -> None:
        """dataclass 完成欄位初始化後執行，限制快取大小並正規化位元組索引。
        Returns:
            None: 正規化初始索引與快取。
        """
        if self._index_cache is not None:
            while len(self._index_cache) > _MAX_CACHED_ENTRIES:
                del self._index_cache[next(iter(self._index_cache))]

        if self._offsets is not None:
            if not isinstance(self._offsets, array) or self._offsets.typecode != "Q":
                self._offsets = array("Q", self._offsets)
            if len(self._offsets) > max(0, self.length):
                del self._offsets[max(0, self.length):]

    def __iter__(self) -> Iterator[LogEntry]:
        """讓 for log in store 逐筆讀取，必要時同步補齊每筆的位元組位置。

        每次迭代重新開檔，因此可供摘要與匯出重複遍歷；yield 會暫停函式，
        下次取值才繼續。cast 只提供型別提示，不會把物件轉成另一種資料。
        Returns:
            Iterator[LogEntry]: 每次迭代重新開檔、逐筆產生群組的生成器。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        """
        if self._offsets is None:
            self._offsets = array("Q")

        with open(self.path, "rb") as file_handle:
            index = 0
            while True:
                indexed = index < self.length and index == len(self._offsets)
                if indexed:
                    self._offsets.append(file_handle.tell())
                try:
                    log = cast(LogEntry, pickle.load(file_handle))
                except EOFError:
                    if indexed:
                        self._offsets.pop()
                    break
                yield log
                index += 1

    def iter_page(self, start: int, size: int) -> Generator[LogEntry, None, None]:
        """從零起算的 start 位置讀取最多 size 筆；負起點視為零，超界回空串流。

        Args:
            start: 從零起算的起點，負數視為零。
            size: 最多讀取的筆數；小於等於零時不產生資料。

        Returns:
            Generator[LogEntry, None, None]: 指定範圍的群組；無有效範圍則為空。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        """
        if size <= 0:
            return

        start = max(0, start)
        if start >= self.length:
            return

        end = min(start + size, self.length)
        yield from self._iter_range(start, end)

    def _iter_range(self, start: int, end: int) -> Iterator[LogEntry]:
        """讀取半開區間 [start, end)，有索引就 seek，否則從已知位置向後補索引。

        seek / tell 使用檔案位元組位置；不必為了尾頁每次從檔頭反序列化。
        with 管理檔案生命週期，生成器結束或關閉時會關閉檔案。

        Args:
            start: 從零起算的起點，負數視為零。
            end: 不包含在結果內的結束索引。

        Returns:
            Iterator[LogEntry]: 半開區間內可讀取的群組。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        """
        if self._offsets is None:
            self._offsets = array("Q")
        offsets = self._offsets

        with open(self.path, "rb") as file_handle:
            if offsets and start < len(offsets):
                file_handle.seek(offsets[start])
                current_index = start
            elif offsets:
                file_handle.seek(offsets[-1])
                try:
                    pickle.load(file_handle)
                except EOFError:
                    return
                current_index = len(offsets)
            else:
                current_index = 0

            while current_index < end:
                appended_offset = current_index == len(offsets)
                if appended_offset:
                    offsets.append(file_handle.tell())
                try:
                    log = cast(LogEntry, pickle.load(file_handle))
                except EOFError:
                    if appended_offset:
                        offsets.pop()
                    break
                if current_index >= start:
                    yield log
                current_index += 1

    def __len__(self) -> int:
        """支援 len(store)，直接讀取保存的筆數，不掃描暫存檔。
        Returns:
            int: 保存的群組數。
        """
        return self.length

    def __getitem__(self, index: int) -> LogEntry:
        """支援 store[index] 與負索引；先讀快取，再以分頁讀一筆。

        超出範圍拋出 IndexError。finally 主動關閉只取一筆的生成器，
        避免檔案仍被占用而影響 Windows 下的暫存清理。

        Args:
            index: 資料索引；是否允許負數依函式說明。

        Returns:
            LogEntry: 指定索引的群組，可能取自快取。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
            IndexError: 索引超界或儲存器缺少對應資料。
        """
        if index < 0:
            index += self.length
        if index < 0 or index >= self.length:
            raise IndexError(index)

        cache = self._index_cache
        if cache is not None and index in cache:
            log = cache.pop(index)
            cache[index] = log
            return log

        page = self.iter_page(index, 1)
        try:
            log = next(page)
        except StopIteration:
            raise IndexError(index) from None
        finally:
            page.close()
        self._remember(index, log)
        return log

    def __bool__(self) -> bool:
        """讓 if store 表示是否有群組，不必實際讀取檔案。
        Returns:
            bool: 群組數是否大於零。
        """
        return self.length > 0

    def __eq__(self, other: object) -> bool:
        """與 list 比較時逐筆轉成 list；其他型別交還 Python 的比較協定。

        Args:
            other: 要比較的另一個物件。

        Returns:
            bool | NotImplementedType: 與 list 比較的結果；其他型別回傳 NotImplemented。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        """
        if isinstance(other, list):
            return list(self) == other
        return NotImplemented

    def __del__(self) -> None:
        """回收物件時的補救清理；執行時機不保證，正常流程仍應主動 release。
        Returns:
            None: 嘗試清理自有暫存。
        """
        if hasattr(self, "path"):
            self.release()

    def release(self) -> None:
        """盡力刪除自有暫存檔並清空快取；不刪來源日誌，也不適合再繼續讀取。
        Returns:
            None: 清空快取並盡力刪除自有暫存。
        """
        try:
            path = getattr(self, "path", "")
            if path and os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        if hasattr(self, "_index_cache"):
            self._index_cache = {}
        if hasattr(self, "_offsets"):
            self._offsets = None

    def _remember(self, index: int, log: LogEntry) -> None:
        """保存最近使用的少量資料，利用 dict 插入順序淘汰最久未使用項目。

        Args:
            index: 資料索引；是否允許負數依函式說明。
            log: 已分組的單筆事件，提供欄位讀取介面。

        Returns:
            None: 原地更新最近使用項目的快取。
        """
        if self._index_cache is None:
            self._index_cache = {}

        cache = self._index_cache
        if index in cache:
            del cache[index]
        cache[index] = log
        while len(cache) > _MAX_CACHED_ENTRIES:
            del cache[next(iter(cache))]
