"""共用追蹤狀態；每次 step 都在同一個擁有者執行緒內完成。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.domain.parsing.aggregation import normalize_keyword, should_include
from log_analyzer.domain.parsing.entries import _is_entry_in_time_range
from log_analyzer.domain.parsing.incremental import IncrementalEntryParser
from log_analyzer.domain.timezone import to_local_timezone
from log_analyzer.infrastructure.sources.incremental import IncrementalFileReader
from log_analyzer.infrastructure.storage.live_events import LiveEventSnapshot, LiveEventStore


# 縮短同步解析與寫入的連續佔用時間，保留暫停與取消的交接點。
LIVE_READ_BATCH_BYTES = 4096


@dataclass(frozen=True, slots=True)
class TrackingSnapshot:
    """保留當時條件與事件截止點，不重新開啟來源檔案。"""

    session_id: str
    conditions: AnalysisRequest
    captured_at: datetime
    events: LiveEventSnapshot
    completed: int
    received_bytes: int
    has_pending: bool
    gaps: tuple[str, ...]

    @property
    def cutoff(self) -> int:
        return self.events.cutoff

    def release(self) -> None:
        """釋放快照持有的暫存參照；不影響仍在追蹤的工作階段。"""
        self.events.release()


class LiveSession:
    """協調單一 reader、parser 與儲存器；暫停保留所有解析緩衝。

    呼叫者以單一事件迴圈序列化 resume、step、pause 與 end。
    step 不含 await，因此 pause 返回後不會有尚未交接的讀取批次。
    close 只釋放資源；只有明確 end 才完成已讀取的尾端事件。
    """

    def __init__(self, conditions: AnalysisRequest, *, directory=None,
                 max_bytes: int = 256 * 1024 * 1024):
        path = Path(conditions.path).expanduser().resolve()
        if path.is_dir():
            raise ValueError("即時追蹤需要單一未壓縮日誌檔案")
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() in {".gz", ".zip", ".bz2", ".xz"}:
            raise ValueError("即時追蹤需要單一未壓縮日誌檔案")
        if conditions.selected_files not in (None, (path.name,)):
            raise ValueError("即時追蹤只允許單一來源檔案")
        self._start_dt = to_local_timezone(conditions.start_dt) if conditions.start_dt else None
        self._end_dt = to_local_timezone(conditions.end_dt) if conditions.end_dt else None
        if self._start_dt and self._end_dt and self._start_dt > self._end_dt:
            raise ValueError("開始時間不能晚於結束時間")
        self.conditions = conditions
        self.session_id = uuid4().hex
        self.path = path
        self._parser = IncrementalEntryParser(path.name, conditions.log_pattern)
        self._reader = IncrementalFileReader(path)
        self._store = LiveEventStore(directory=directory, max_bytes=max_bytes)
        self._keyword = normalize_keyword(conditions.keyword, conditions.ignore_case)
        self._generation = 0
        self.state = "paused"
        self.completed = 0
        self.received_bytes = 0
        self.level_counts: Counter = Counter()
        self.gaps: list[str] = []
        self.error: str | None = None
        self.last_read_at: datetime | None = None
        self._closed = False

    @property
    def offset(self) -> int:
        return self._reader.offset

    @property
    def backlog(self) -> int:
        return self._reader.backlog

    @property
    def matched(self) -> int:
        return len(self._store)

    @property
    def pending(self):
        return self._parser.pending

    @property
    def has_pending(self) -> bool:
        """尚未形成完整首行的內容也算接收中，不列入完成筆數。"""
        return self._parser.has_pending_input

    def resume(self, conditions: AnalysisRequest | None = None) -> None:
        """條件相同才恢復；下一次讀取主動核對來源世代。

        Args:
            conditions: 目前表單條件；None 沿用原條件。

        Raises:
            ValueError: 條件改變，需明確重新分析來源。
            RuntimeError: 工作階段已關閉、結束或遭遇無法續讀的錯誤。
        """
        if conditions is not None and conditions != self.conditions:
            raise ValueError("條件已變更，請使用「重新分析來源」")
        if self._closed or self.state in {"stopped", "error"}:
            raise RuntimeError("此工作階段不能接續，請重新分析來源")
        self.state = "tracking"

    def pause(self) -> None:
        """在同步讀取批次間暫停；不呼叫 finish，也不讀取來源。"""
        if self.state in {"tracking", "interrupted"}:
            self.state = "paused"

    def step(self, max_bytes: int = LIVE_READ_BATCH_BYTES) -> list[dict]:
        """讀取有限 bytes，依序保存已完成且命中的事件。

        Args:
            max_bytes: 本批讀取上限；呼叫者在批次之間讓出控制權。

        Returns:
            list[dict]: 本批新保存的事件，含穩定接收序號與來源世代。

        Raises:
            OSError: 讀取或保存失敗；狀態轉為 error，既有結果仍可查看。
        """
        if self.state not in {"tracking", "interrupted"}:
            return []
        try:
            return self._read_batch(max_bytes)
        except OSError as exc:
            self.error = str(exc)
            self.state = "error"
            self.gaps.append("讀取或儲存失敗，本批資料可能未完整保存；需重新分析來源。")
            raise

    def _read_batch(self, max_bytes: int) -> list[dict]:
        batch = self._reader.read(max_bytes)
        result = []
        if batch.gap:
            self.gaps.append(batch.gap)
        if batch.reset:
            # 舊 handle 已排空或確認重設；不可把兩個世代的續行拼接。
            result.extend(self._accept(self._parser.finish()))
            self._generation = batch.generation
            self._parser = IncrementalEntryParser(self.path.name, self.conditions.log_pattern)
        if batch.data:
            self.received_bytes += len(batch.data)
            self.last_read_at = datetime.now(timezone.utc)
            result.extend(self._accept(self._parser.feed(batch.data)))
        self.state = "interrupted" if batch.missing else "tracking"
        return result

    def _accept(self, entries) -> list[dict]:
        accepted = []
        for entry in entries:
            self.completed += 1
            if not self._matches(entry):
                continue
            sequence = self._store.append(entry, self._generation)
            self.level_counts[entry["level"]] += 1
            accepted.append({**entry, "sequence": sequence, "generation": self._generation})
        return accepted

    def _matches(self, entry) -> bool:
        conditions = self.conditions
        if conditions.levels is not None and entry["level"] not in conditions.levels:
            return False
        if not _is_entry_in_time_range(entry, self._start_dt, self._end_dt):
            return False
        return should_include(entry["full_text"], self._keyword, conditions.ignore_case)

    def snapshot(self) -> TrackingSnapshot:
        """固定已保存事件，不讀來源、不 finish、不變更追蹤狀態。"""
        return TrackingSnapshot(
            self.session_id, self.conditions, datetime.now(timezone.utc),
            self._store.snapshot(), self.completed, self.received_bytes,
            self.has_pending, tuple(self.gaps),
        )

    def iter_page(self, start: int, size: int):
        """依接收順序讀取有限範圍的已保存事件。"""
        return self._store.iter_page(start, size)

    def end(self) -> list[dict]:
        """以已讀 bytes 為明確截止點結束；不追讀未來追加內容。"""
        if self._closed or self.state == "stopped":
            return []
        if self.state == "error":
            self._reader.close()
            return []
        self.state = "stopped"
        try:
            return self._accept(self._parser.finish())
        except OSError as exc:
            self.error = str(exc)
            self.state = "error"
            self.gaps.append("結束時無法保存尾端事件。")
            raise
        finally:
            self._reader.close()

    def close(self) -> None:
        """釋放來源與暫存；既有快照依其自身生命週期保留。"""
        if not self._closed:
            self._closed = True
            self._reader.close()
            self._store.close()
