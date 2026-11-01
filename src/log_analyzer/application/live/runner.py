"""Async orchestration for a single live analysis session."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import aclosing
from typing import Any

from log_analyzer.application.live.session import LIVE_READ_BATCH_BYTES, LiveSession
from log_analyzer.infrastructure.sources.watcher import watch_source


_READ_BATCH_BYTES = LIVE_READ_BATCH_BYTES


class LiveRunner:
    """在單一事件迴圈中驅動工作階段，並依序執行控制操作。

    Args:
        session: 唯一由 runner 讀取的即時工作階段。
        on_events: 每批新保存事件的同步通知函式。
        on_update: 狀態或錯誤更新時呼叫的同步通知函式。
    """

    def __init__(
        self,
        session: LiveSession,
        on_events: Callable[[list[dict[str, Any]]], None] | None = None,
        on_update: Callable[[], None] | None = None,
    ) -> None:
        """建立 runner。

        Args:
            session: 唯一由 runner 讀取的即時工作階段。
            on_events: 每批新保存事件的同步通知函式。
            on_update: 狀態或錯誤更新時呼叫的同步通知函式。

        Returns:
            None: 初始化 runner。
        """
        self.session = session
        self.on_events = on_events
        self.on_update = on_update
        self._lock = asyncio.Lock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._drive_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._failure: Exception | None = None

    @property
    def running(self) -> bool:
        """查詢背景讀取工作是否仍在執行。

        Returns:
            bool: 背景工作尚未結束時為 True。
        """
        task = self._drive_task
        return task is not None and not task.done()

    @property
    def failure(self) -> Exception | None:
        """取得背景讀取或同步通知函式遇到的第一個錯誤。

        Returns:
            Exception | None: 第一個錯誤；沒有錯誤時為 None。
        """
        return self._failure

    async def start(self, conditions=None) -> None:
        """驗證條件並恢復追蹤，隨即排程第一批讀取。

        Args:
            conditions: 要使用的分析條件；省略時沿用工作階段原條件。

        Returns:
            None: 工作已排程後立即返回。

        Raises:
            ValueError: 分析條件已變更。
            RuntimeError: runner 已綁定到另一個事件迴圈，或工作階段不能恢復。
        """
        self._check_owner_loop()
        async with self._lock:
            self.session.resume(conditions)
            if self.running:
                return
            self._failure = None
            self._stop_event = asyncio.Event()
            task = asyncio.create_task(
                self._drive(self._stop_event),
                name="live-session-runner",
            )
            task.add_done_callback(self._drive_finished)
            self._drive_task = task

    async def pause(self) -> None:
        """停止監看並等待目前批次交接；保留 parser 尾端，不完成事件。

        Returns:
            None: 背景工作已停止，工作階段保留未完成尾端。

        Raises:
            RuntimeError: runner 已綁定到另一個事件迴圈。
            asyncio.CancelledError: 呼叫者取消等待；安全暫停完成後重新拋出。
        """
        self._check_owner_loop()
        async with self._lock:
            cancellation = await self._stop_drive_locked()
            self.session.pause()
            self._notify_update()
            if cancellation is not None:
                raise cancellation

    async def stop(self) -> list[dict[str, Any]]:
        """暫停並明確完成已讀內容，最後一次交付尾端事件。

        Returns:
            list[dict[str, Any]]: 這次結束時新完成並保存的事件。

        Raises:
            OSError: 結束事件保存失敗；已保存的前綴仍會先通知呼叫者。
            RuntimeError: runner 已綁定到另一個事件迴圈。
            asyncio.CancelledError: 呼叫者取消等待；安全交接完成後重新拋出。
        """
        self._check_owner_loop()
        async with self._lock:
            cancellation = await self._stop_drive_locked()
            self.session.pause()
            previous_matched = self.session.matched
            try:
                events = self.session.end()
            except OSError as exc:
                try:
                    self._deliver_saved_prefix(previous_matched)
                except Exception as callback_error:
                    self._record_failure(exc)
                    self._record_failure(callback_error)
                    self._notify_update()
                    raise exc from callback_error
                self._record_failure(exc)
                self._notify_update()
                raise
            try:
                self._deliver_events(events)
            except Exception as exc:  # noqa: BLE001 - report synchronous callback failures
                self._record_failure(exc)
            self._notify_update()
            if cancellation is not None:
                raise cancellation
            return events

    async def _stop_drive_locked(self) -> asyncio.CancelledError | None:
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._drive_task
        cancellation = None
        if task is not None:
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError as exc:
                        if task.done():
                            if not task.cancelled():
                                cancellation = cancellation or exc
                            elif asyncio.current_task().cancelling():
                                cancellation = cancellation or exc
                            else:
                                self._record_failure(
                                    RuntimeError(
                                        "live drive task was cancelled unexpectedly"
                                    )
                                )
                            break
                        cancellation = cancellation or exc
                    except Exception as exc:  # noqa: BLE001 - retain task failures as observable
                        self._record_failure(exc)
                        break
                if task.done() and not task.cancelled():
                    try:
                        task.result()
                    except Exception as exc:  # noqa: BLE001 - retain task failures as observable
                        self._record_failure(exc)
            finally:
                self._drive_task = None
        self._stop_event = None
        return cancellation

    def _check_owner_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._owner_loop is None:
            self._owner_loop = loop
        elif self._owner_loop is not loop:
            raise RuntimeError("LiveRunner must be used from one event loop")

    def _drive_finished(self, task: asyncio.Task[None]) -> None:
        if task is not self._drive_task:
            return
        if task.cancelled() and self._failure is None:
            self._record_failure(
                RuntimeError("live drive task was cancelled unexpectedly")
            )
        if self._failure is not None:
            self._notify_update()

    async def _drive(self, stop_event: asyncio.Event) -> None:
        watcher = watch_source(self.session.path, stop_event)
        watch_task: asyncio.Task[None] | None = None
        try:
            async with aclosing(watcher):
                try:
                    watch_task = asyncio.create_task(anext(watcher))
                    while not stop_event.is_set():
                        previous_matched = self.session.matched
                        try:
                            events = self.session.step(_READ_BATCH_BYTES)
                        except OSError as exc:
                            try:
                                self._deliver_saved_prefix(previous_matched)
                            except Exception as recovery_error:  # noqa: BLE001 - preserve the read failure
                                self._record_failure(exc)
                                self._record_failure(recovery_error)
                                self._notify_update()
                                return
                            self._record_failure(exc)
                            self._notify_update()
                            return

                        self._deliver_events(events)
                        if not self._notify_update():
                            return
                        if self.session.state == "error":
                            message = (
                                self.session.error or "live session entered error state"
                            )
                            self._record_failure(OSError(message))
                            self._notify_update()
                            return
                        if stop_event.is_set():
                            break

                        if self.session.backlog > 0:
                            await asyncio.sleep(0)
                            continue

                        try:
                            await watch_task
                        except StopAsyncIteration:
                            if stop_event.is_set():
                                break
                            raise RuntimeError("source watcher stopped unexpectedly")
                        watch_task = asyncio.create_task(anext(watcher))
                finally:
                    await self._finish_watcher_task(watch_task)
        except OSError as exc:
            self._record_failure(exc)
            self._notify_update()
        except Exception as exc:  # noqa: BLE001 - keep background task failures observable
            self._record_failure(exc)
            self._notify_update()

    async def _finish_watcher_task(
        self,
        watch_task: asyncio.Task[None] | None,
    ) -> None:
        if watch_task is None:
            return
        if not watch_task.done():
            watch_task.cancel()
        try:
            await watch_task
        except (asyncio.CancelledError, StopAsyncIteration):
            return
        except OSError as exc:
            self._record_failure(exc)
            self._notify_update()
        except RuntimeError as exc:
            self._record_failure(exc)
            self._notify_update()

    def _deliver_saved_prefix(self, previous_matched: int) -> None:
        saved_count = self.session.matched - previous_matched
        if saved_count > 0:
            events = list(self.session.iter_page(previous_matched, saved_count))
            self._deliver_events(events)

    def _deliver_events(self, events) -> None:
        batch = list(events)
        if batch and self.on_events is not None:
            self.on_events(batch)

    def _notify_update(self) -> bool:
        if self.on_update is None:
            return True
        try:
            self.on_update()
        except Exception as exc:  # noqa: BLE001 - report synchronous callback failures
            self._record_failure(exc)
            return False
        return True

    def _record_failure(self, error: Exception) -> None:
        if self._failure is None:
            self._failure = error
        if getattr(self.session, "error", None) is None:
            self.session.error = str(error)
        if getattr(self.session, "state", None) not in {"error", "stopped"}:
            self.session.state = "error"
