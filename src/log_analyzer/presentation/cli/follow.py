"""CLI presentation for a single-file live tracking session."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.runner import LiveRunner
from log_analyzer.application.live.session import LiveSession
from log_analyzer.domain.log_sources import select_log_source

from .runtime import (
    parse_datetime_value,
    resolve_levels,
    resolve_logback_pattern,
    resolve_target_dir,
)


def _resolve_follow_conditions(args) -> tuple[AnalysisRequest, tuple[str, ...]]:
    """解析單一日誌來源及 follow 固定條件。

    Args:
        args: argparse 解析後且已通過 follow 互斥檢查的參數。

    Returns:
        tuple[AnalysisRequest, tuple[str, ...]]: 固定分析條件與要送往 stderr 的提示。

    Raises:
        FileNotFoundError: 指定來源不存在。
        PermissionError: 指定來源無法讀取。
        ValueError: 來源不是單一檔案，或日期、Level、Pattern 設定無效。
    """
    target_dir, source_notice = resolve_target_dir(args.dir)
    source = select_log_source(target_dir)
    if not source.is_file or len(source.filenames) != 1:
        raise ValueError("--follow 需要指定單一日誌檔案；不支援資料夾來源")

    start_dt = parse_datetime_value(args.start, "開始時間")
    end_dt = parse_datetime_value(args.end, "結束時間")
    log_pattern, pattern_notice = resolve_logback_pattern(
        args.logback_xml,
        source.path,
        args.pattern,
        selected_files=source.filenames,
    )
    conditions = AnalysisRequest(
        path=source.path,
        start_dt=start_dt,
        end_dt=end_dt,
        keyword=args.keyword,
        ignore_case=args.ignore_case,
        sort_by=args.sort,
        log_pattern=log_pattern,
        levels=resolve_levels(args.level),
        selected_files=source.filenames,
    )
    notices = tuple(message for message in (source_notice, pattern_notice) if message)
    return conditions, notices


class _FollowOutput:
    """負責 follow 事件與狀態的 stdout／stderr 分流。"""

    def __init__(self, session: LiveSession, updates: asyncio.Event) -> None:
        """保存狀態回呼所需的 session 與喚醒事件。

        Args:
            session: 本次 CLI 持有的即時工作階段。
            updates: 通知主 coroutine 狀態有更新的事件。

        Returns:
            None: 建立輸出回呼狀態。
        """
        self._session = session
        self._updates = updates
        self._last_pending = False
        self._last_state = "tracking"
        self._reported_gap_count = 0

    def on_events(self, events: list[dict[str, Any]]) -> None:
        """依序輸出 runner 交付的完整事件原文。

        Args:
            events: runner 本次完成並交付的事件。

        Returns:
            None: 將事件原文寫到 stdout 並立即 flush。
        """
        for event in events:
            raw_text = event.get("raw_text", "")
            if raw_text:
                sys.stdout.write(raw_text)
        sys.stdout.flush()

    def on_update(self) -> None:
        """將追蹤狀態與待完成內容提示送至 stderr。

        Returns:
            None: 必要時輸出一次提示，並喚醒等待中的 coroutine。
        """
        current_pending = bool(self._session.has_pending)
        if current_pending and not self._last_pending:
            print("有尚未完成的日誌內容；完成前不會輸出。", file=sys.stderr, flush=True)
        self._last_pending = current_pending

        gaps = self._session.gaps
        for gap in gaps[self._reported_gap_count :]:
            print(f"資料缺口：{gap}", file=sys.stderr, flush=True)
        self._reported_gap_count = len(gaps)

        current_state = self._session.state
        message = {
            "interrupted": "來源暫時中斷，等待恢復。",
            "tracking": "來源已恢復，追蹤繼續。",
            "stopped": "追蹤已停止。",
        }.get(current_state)
        if current_state != self._last_state and message:
            print(message, file=sys.stderr, flush=True)
        self._last_state = current_state
        self._updates.set()


async def _wait_for_runner(
    runner: LiveRunner,
    conditions: AnalysisRequest,
    updates: asyncio.Event,
) -> None:
    """等待 runner 停止，並在自然失敗時傳回原始例外。

    Args:
        runner: 已建立的追蹤 runner。
        conditions: 建立 session 時固定的條件。
        updates: runner 狀態更新通知事件。

    Returns:
        None: runner 停止且沒有記錄錯誤時返回。

    Raises:
        Exception: runner 記錄的原始失敗。
    """
    await runner.start(conditions)
    while runner.running:
        updates.clear()
        if not runner.running:
            break
        await updates.wait()
    if runner.failure is not None:
        raise runner.failure


async def _stop_runner_and_close(
    runner: LiveRunner | None,
    session: LiveSession,
    primary_error: BaseException | None,
) -> list[BaseException]:
    """停止 runner 並釋放 session，回傳清理期間發生的錯誤。

    Args:
        runner: 可能尚未啟動的 runner。
        session: 由 CLI 建立並持有的 session。
        primary_error: 追蹤期間發生的主要錯誤。

    Returns:
        list[BaseException]: stop、runner failure 或 close 期間的錯誤。
    """
    errors: list[BaseException] = []
    if runner is not None:
        try:
            await runner.stop()
        except (Exception, asyncio.CancelledError, KeyboardInterrupt) as exc:  # noqa: BLE001
            # 即使 stop 失敗仍須執行 session.close，並在清理後回報原錯誤。
            errors.append(exc)
        if runner.failure is not None and runner.failure is not primary_error:
            errors.append(runner.failure)

    try:
        session.close()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        # 保留 close 錯誤，避免把失敗的清理誤報為正常結束。
        errors.append(exc)
    return errors


def _raise_follow_errors(
    primary_error: BaseException | None,
    cleanup_errors: list[BaseException],
) -> None:
    """保留主要錯誤，並避免清理錯誤被取消狀態或彼此掩蓋。

    Args:
        primary_error: 追蹤或取消期間的錯誤。
        cleanup_errors: 停止 runner 或釋放 session 期間的錯誤。

    Returns:
        None: 沒有主要錯誤或清理錯誤時返回。

    Raises:
        BaseException: 追蹤失敗，或中斷清理時也發生失敗。
    """
    if primary_error is None and cleanup_errors:
        primary_error = cleanup_errors.pop(0)
    elif isinstance(primary_error, (asyncio.CancelledError, KeyboardInterrupt)) and cleanup_errors:
        primary_error = cleanup_errors.pop(0)

    for cleanup_error in cleanup_errors:
        print(f"清理追蹤資源時發生錯誤：{cleanup_error}", file=sys.stderr)
    if primary_error is not None:
        raise primary_error


async def run_follow(args) -> None:
    """依固定條件追蹤單一日誌檔案，將完成事件寫到 stdout。

    Args:
        args: argparse 解析後且已通過 follow 互斥檢查的參數。

    Returns:
        None: 追蹤因 runner 停止或呼叫端取消而完成清理。

    Raises:
        OSError: 來源讀取、事件儲存或輸出失敗。
        ValueError: 來源、日期、Level 或 Pattern 設定無效。
        RuntimeError: 追蹤工作階段無法啟動或接續。
    """
    conditions, notices = _resolve_follow_conditions(args)
    session = LiveSession(conditions)
    updates = asyncio.Event()
    output = _FollowOutput(session, updates)
    runner: LiveRunner | None = None
    primary_error: BaseException | None = None

    try:
        runner = LiveRunner(session, on_events=output.on_events, on_update=output.on_update)
        for notice in notices:
            print(notice, file=sys.stderr)
        print(f"持續追蹤中：{conditions.path}", file=sys.stderr, flush=True)
        await _wait_for_runner(runner, conditions, updates)
    except asyncio.CancelledError as exc:
        primary_error = exc
    except KeyboardInterrupt as exc:
        primary_error = exc
    except Exception as exc:  # noqa: BLE001 - delay reporting until resources are closed
        primary_error = exc

    cleanup_errors = await _stop_runner_and_close(runner, session, primary_error)
    _raise_follow_errors(primary_error, cleanup_errors)
