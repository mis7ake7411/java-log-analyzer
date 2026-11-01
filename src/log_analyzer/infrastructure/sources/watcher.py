"""Translate filesystem changes into hints for the incremental reader."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path

from watchfiles import awatch


async def watch_source(
    path: str | Path,
    stop_event: asyncio.Event,
) -> AsyncIterator[None]:
    """監看指定來源並在變更或定期補查時送出提示，不讀取檔案內容。

    Args:
        path: 要監看的日誌檔案路徑；只回報此路徑的變更。
        stop_event: 設定後停止等待並結束非同步迭代。

    Returns:
        AsyncIterator[None]: 逐次傳遞檔案變更或補查提示的非同步迭代器。

    Yields:
        None: 目標檔案變更，或定期補查時間到期。

    Raises:
        OSError: 無法建立或維持來源目錄監看時。
    """
    target = Path(path).expanduser().resolve()

    def is_target(_change, changed_path: str) -> bool:
        try:
            return Path(changed_path).resolve() == target
        except (OSError, RuntimeError):
            return False

    watcher = awatch(
        str(target.parent),
        watch_filter=is_target,
        stop_event=stop_event,
        recursive=False,
        rust_timeout=500,
        yield_on_timeout=True,
    )
    async with aclosing(watcher):
        async for _changes in watcher:
            yield None
