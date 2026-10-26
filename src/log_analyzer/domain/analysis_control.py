"""介面與背景分析的溝通物件：以 Event 要求取消，以回呼回報進度。

取消採合作方式：cancel 只設定訊號，解析器到檢查點才拋例外停止。
進度回呼在分析呼叫的執行緒執行，不能直接操作 TUI 畫面。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

PROGRESS_LINE_INTERVAL = 128
_UNSET = object()


class AnalysisCancelled(RuntimeError):
    """解析器在檢查點觀察到取消要求時拋出的例外，與一般分析失敗分開處理。"""


@dataclass(frozen=True, slots=True)
class AnalysisProgress:
    """某一刻的階段、檔名與處理計數；frozen=True 避免通知後被修改。"""

    phase: str
    filename: str | None
    files_completed: int
    files_total: int
    lines_processed: int


class AnalysisControl:
    """使用可跨執行緒的取消訊號，並節流進度通知以減少畫面更新次數。"""

    def __init__(
        self,
        progress_callback: Callable[[AnalysisProgress], None] | None = None,
    ) -> None:
        """建立取消 Event 與可選的進度回呼；未提供回呼仍可查詢最新進度。

        Args:
            progress_callback: 進度回呼；None 表示只保存進度，不通知外部。

        Returns:
            None: 建立取消訊號與初始進度狀態。
        """
        self._cancel_event = Event()
        self._progress_callback = progress_callback
        self._latest_progress: AnalysisProgress | None = None
        self._last_notified_progress: AnalysisProgress | None = None

    @property
    def is_cancelled(self) -> bool:
        """以 property 提供唯讀查詢，呼叫端使用 control.is_cancelled，不加括號。
        Returns:
            bool: 是否已收到取消要求。
        """
        return self._cancel_event.is_set()

    @property
    def latest_progress(self) -> AnalysisProgress | None:
        """取得最後記錄的快照；尚未回報任何進度時為 None。
        Returns:
            AnalysisProgress | None: 最新快照，尚未回報時為 None。
        """
        return self._latest_progress

    def cancel(self) -> None:
        """設定停止要求；不會強制終止執行緒或中斷正在進行的檔案操作。
        Returns:
            None: 只設定訊號，不強制中止工作。
        """
        self._cancel_event.set()

    def raise_if_cancelled(self) -> None:
        """在安全檢查點將取消訊號轉成 AnalysisCancelled，讓上層執行清理。
        Returns:
            None: 沒有取消要求時繼續。

        Raises:
            AnalysisCancelled: 已收到取消要求。
        """
        if self._cancel_event.is_set():
            raise AnalysisCancelled("分析已取消")

    def report_progress(  # noqa: PLR0913 - explicit keyword fields mirror the progress snapshot
        self,
        phase: str,
        *,
        filename: str | None | object = _UNSET,
        files_completed: int | None = None,
        files_total: int | None = None,
        lines_processed: int | None = None,
        force: bool = False,
    ) -> None:
        """更新進度快照，階段／檔案改變或累積足夠行數時通知呼叫者。

        星號後的欄位只能用具名參數傳入；未提供的計數沿用上次值。
        filename 用獨立 _UNSET 區別「沒有傳入」與「明確設為 None」。
        force=True 強制通知，但仍會先檢查取消。

        Args:
            phase: 目前分析階段名稱，例如 scanning 或 sorting。
            filename: 目前檔名；未提供沿用前值，None 明確清除。
            files_completed: 已完成檔數；None 沿用前值。
            files_total: 總檔數；None 沿用前值。
            lines_processed: 累積處理行數；None 沿用前值。
            force: 是否略過節流門檻立即通知。

        Returns:
            None: 保存快照，符合通知條件時同步呼叫回呼。

        Raises:
            AnalysisCancelled: 建立進度前已收到取消要求。
        """
        self.raise_if_cancelled()
        previous = self._latest_progress
        progress = AnalysisProgress(
            phase=phase,
            filename=(previous.filename if previous else None) if filename is _UNSET else filename,
            files_completed=(previous.files_completed if previous else 0)
            if files_completed is None
            else files_completed,
            files_total=(previous.files_total if previous else 0) if files_total is None else files_total,
            lines_processed=(previous.lines_processed if previous else 0)
            if lines_processed is None
            else lines_processed,
        )
        self._latest_progress = progress

        callback = self._progress_callback
        if callback is None:
            return

        last_notified = self._last_notified_progress
        should_notify = (
            force
            or last_notified is None
            or progress.phase != last_notified.phase
            or progress.filename != last_notified.filename
            or progress.files_completed != last_notified.files_completed
            or progress.files_total != last_notified.files_total
            or progress.lines_processed - last_notified.lines_processed >= PROGRESS_LINE_INTERVAL
        )
        if should_notify:
            self._last_notified_progress = progress
            callback(progress)
