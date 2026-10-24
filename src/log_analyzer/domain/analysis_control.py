from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

PROGRESS_LINE_INTERVAL = 128
_UNSET = object()


class AnalysisCancelled(RuntimeError):
    """Raised when a cooperative analysis cancellation is observed."""


@dataclass(frozen=True, slots=True)
class AnalysisProgress:
    """A point-in-time snapshot of analysis work completed so far."""

    phase: str
    filename: str | None
    files_completed: int
    files_total: int
    lines_processed: int


class AnalysisControl:
    """Thread-safe cancellation signal with throttled progress notifications."""

    def __init__(
        self,
        progress_callback: Callable[[AnalysisProgress], None] | None = None,
    ) -> None:
        self._cancel_event = Event()
        self._progress_callback = progress_callback
        self._latest_progress: AnalysisProgress | None = None
        self._last_notified_progress: AnalysisProgress | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def latest_progress(self) -> AnalysisProgress | None:
        return self._latest_progress

    def cancel(self) -> None:
        self._cancel_event.set()

    def raise_if_cancelled(self) -> None:
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
