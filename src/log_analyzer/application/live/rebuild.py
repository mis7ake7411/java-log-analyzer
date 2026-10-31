"""以固定初始截止點準備新階段，成功前不更動舊結果。"""

from __future__ import annotations

import asyncio

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.session import LIVE_READ_BATCH_BYTES, LiveSession


async def prepare_replacement(
    previous: LiveSession,
    conditions: AnalysisRequest,
    *,
    directory=None,
    max_bytes: int = 256 * 1024 * 1024,
) -> LiveSession:
    """準備可供替換的新階段；呼叫者在發布新階段後才釋放 previous。

    舊 runner 必須先完成 pause。新階段只讀到第一批結束時量得的來源
    大小，之後追加的 bytes 留待接續，不讓持續寫入阻止重建完成。
    成功後保持暫停、保留待完成事件；取消或失敗只清理候選階段。

    Args:
        previous: 已暫停或錯誤的舊工作階段，不會由本函式釋放。
        conditions: 新來源與固定分析條件。
        directory: 新階段事件暫存位置。
        max_bytes: 新階段的事件 payload 容量上限。

    Returns:
        LiveSession: 初始讀取完成且已暫停的新階段。

    Raises:
        RuntimeError: 舊階段尚未安全暫停。
        OSError: 新來源讀取、世代核對或結果保存失敗。
        ValueError: 來源或分析條件無效。
        asyncio.CancelledError: 使用者取消準備工作。
    """
    if previous.state not in {"paused", "error", "stopped"}:
        raise RuntimeError("請先完成暫停，再重新分析來源")
    candidate = LiveSession(conditions, directory=directory, max_bytes=max_bytes)
    try:
        before = candidate.path.stat()
        identity = (before.st_dev, before.st_ino)
        candidate.resume()
        candidate.step()
        cutoff = candidate.offset + candidate.backlog
        _check_source(candidate, identity)
        await asyncio.sleep(0)
        while candidate.offset < cutoff and candidate.backlog:
            candidate.step(min(LIVE_READ_BATCH_BYTES, cutoff - candidate.offset))
            _check_source(candidate, identity)
            await asyncio.sleep(0)
        _check_source(candidate, identity)
        candidate.pause()
        return candidate
    except BaseException:
        candidate.close()
        raise


def _check_source(candidate: LiveSession, identity: tuple[int, int]) -> None:
    current = candidate.path.stat()
    if (
        (current.st_dev, current.st_ino) != identity
        or current.st_size < candidate.offset
        or candidate.gaps
        or candidate.state == "interrupted"
    ):
        raise OSError("來源在重新分析期間輪替、截斷或中斷；已保留舊追蹤結果")
