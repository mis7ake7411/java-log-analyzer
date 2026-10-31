"""將固定追蹤快照彙整為可瀏覽與匯出的分析結果。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from log_analyzer.application.analysis.service import AnalysisRequest, AnalysisResult
from log_analyzer.application.analysis.summary import aggregate_dashboard_summary
from log_analyzer.application.live.session import TrackingSnapshot
from log_analyzer.domain.analysis_control import AnalysisControl
from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.infrastructure.storage.live_summary import LiveSummaryStore


@dataclass(slots=True)
class TrackingAnalysisResult(AnalysisResult):
    """含固定追蹤快照的分析結果；明細儲存器釋放時也會釋放快照。"""

    tracking_snapshot: TrackingSnapshot | None = None


class _TrackingMatchedLogsStore:
    """把追蹤摘要磁碟結果與快照生命週期綁定的可分頁介面。"""

    __slots__ = ("_released", "_snapshot", "_store")

    def __init__(self, store: LiveSummaryStore, snapshot: TrackingSnapshot) -> None:
        self._store = store
        self._snapshot = snapshot
        self._released = False

    def __len__(self) -> int:
        """回傳摘要群組數。"""
        return len(self._store)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """依摘要固定順序逐組讀取，可重複遍歷。"""
        return self.iter_page(0, len(self))

    def __getitem__(self, index: int) -> dict[str, Any]:
        """以整數索引讀取摘要群組，支援負索引。"""
        self._ensure_active()
        return self._store[index]

    def iter_page(self, start: int, size: int) -> Iterator[dict[str, Any]]:
        """讀取指定頁面中的摘要群組。"""
        self._ensure_active()
        return self._store.iter_page(start, size)

    def release(self) -> None:
        """釋放摘要暫存與其擁有的追蹤快照；重複呼叫安全。"""
        if self._released:
            return
        try:
            self._store.release()
        finally:
            self._snapshot.release()
        self._released = True

    def _ensure_active(self) -> None:
        if self._released:
            raise RuntimeError("tracking summary result has been released")


def build_tracking_summary(
    snapshot: TrackingSnapshot,
    *,
    control: AnalysisControl | None = None,
) -> TrackingAnalysisResult:
    """僅根據固定追蹤快照建立磁碟分組摘要，不讀取來源日誌。

    Args:
        snapshot: 呼叫端交付的固定追蹤快照；成功後由回傳結果持有。
        control: 可選取消控制器；取消時清理候選摘要但保留快照所有權給呼叫端。

    Returns:
        TrackingAnalysisResult: 可重複瀏覽、分頁及匯出的追蹤摘要。

    Raises:
        AnalysisCancelled: control 收到取消要求時停止彙整。
        OSError: 快照或摘要暫存無法讀寫時向上傳遞。
        ValueError: 快照事件數與其固定截止序號不一致。
    """
    if control is not None:
        control.raise_if_cancelled()

    store = LiveSummaryStore()
    try:
        counts: Counter[str] = Counter()
        observed_occurrences = 0
        events = iter(snapshot.events)
        try:
            for event in events:
                if control is not None:
                    control.raise_if_cancelled()
                store.add_event(event)
                counts[str(event["level"])] += 1
                observed_occurrences += 1
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()

        if observed_occurrences != snapshot.cutoff:
            raise ValueError("追蹤快照事件數與固定截止序號不一致")
        if control is not None:
            control.raise_if_cancelled()

        store.finalize(snapshot.conditions.sort_by)
        if control is not None:
            control.raise_if_cancelled()

        matched_logs = _TrackingMatchedLogsStore(store, snapshot)
        dashboard_summary, _dashboard_occurrences = aggregate_dashboard_summary(
            matched_logs,
            control=control,
        )
        if control is not None:
            control.raise_if_cancelled()

        request: AnalysisRequest = snapshot.conditions
        return TrackingAnalysisResult(
            input_path=request.path,
            output_path="",
            format_name="未匯出",
            keyword=request.keyword or "未設定",
            ignore_case=request.ignore_case,
            total_logs=snapshot.completed,
            matched_groups=len(matched_logs),
            matched_occurrences=snapshot.cutoff,
            level_summary=[
                (level, count)
                for level, count in sorted(counts.items())
                if count > 0
            ],
            selected_levels=tuple(request.levels or ()),
            sort_by=request.sort_by,
            counts=counts,
            matched_logs=matched_logs,
            exported_files=[],
            dashboard_summary=dashboard_summary,
            source_states={},
            diagnostics=ScanDiagnostics(),
            analysis_request=request,
            tracking_snapshot=snapshot,
        )
    except BaseException as error:
        try:
            store.release()
        except OSError as cleanup_error:
            error.add_note(f"清理候選追蹤摘要時也發生錯誤：{cleanup_error}")
        raise
