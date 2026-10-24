"""TUI 測試的完成條件；不重試操作、不改寫畫面，也不取代業務斷言。"""

import asyncio
from collections.abc import Callable, Iterable

from textual.errors import NoWidget


DEFAULT_TIMEOUT = 5.0
OPERATION_TIMEOUT = 10.0
POLL_INTERVAL = 0.01


async def wait_until(
    predicate: Callable[[], bool],
    message: str = "operation did not reach the expected state",
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """只將自己的期限轉成斷言；predicate 的例外直接傳回。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError(f"{message} (timeout={timeout}s)")
        await asyncio.sleep(min(POLL_INTERVAL, remaining))


async def wait_for_ui_state(pilot, predicate, message, *, timeout=DEFAULT_TIMEOUT):
    await wait_until(predicate, message, timeout=timeout)
    await asyncio.wait_for(pilot.pause(), timeout=timeout)
    # 處理佇列期間條件可能再改變，不以舊狀態當作完成。
    await wait_until(predicate, message, timeout=timeout)


async def wait_for_scheduled_analysis(app, previous_task=None, *, timeout=OPERATION_TIMEOUT):
    deadline = asyncio.get_running_loop().time() + timeout
    await wait_until(
        lambda: (
            getattr(app, "_scheduled_analysis_task", None) is not None
            and app._scheduled_analysis_task is not previous_task
        ),
        "analysis task was not scheduled",
        timeout=timeout,
    )
    task = app._scheduled_analysis_task
    remaining = max(0, deadline - asyncio.get_running_loop().time())
    done, _ = await asyncio.wait({task}, timeout=remaining)
    if not done:
        raise AssertionError(f"analysis task did not finish (timeout={timeout}s)")
    # 不吞掉工作本身的例外，也不因等待逾時取消產品工作。
    await task


def _layout_snapshot(widgets):
    return tuple(
        (widget.region, widget.display, tuple(sorted(widget.classes)),
         widget.content_size, widget.scroll_offset, widget.max_scroll_y)
        for widget in widgets
    )


async def wait_for_layout_to_settle(
    pilot, widgets: Iterable, ready=None, *, timeout=DEFAULT_TIMEOUT
):
    widgets = tuple(widgets)
    last_layout = None

    async def settle():
        nonlocal last_layout
        while True:
            await pilot.pause()
            await pilot.wait_for_scheduled_animations()
            layout = _layout_snapshot(widgets)
            is_ready = ready() if ready is not None else all(
                any(not node.display for node in widget.ancestors_with_self)
                or (widget.region.width > 0 and widget.region.height > 0)
                for widget in widgets
            )
            if is_ready and layout == last_layout:
                return
            last_layout = layout if is_ready else None
            await asyncio.sleep(POLL_INTERVAL)

    # wait() 讓框架本身的 TimeoutError 保留原樣；只有本 helper 的期限被轉譯。
    task = asyncio.create_task(settle())
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            raise AssertionError(
                f"layout did not settle (timeout={timeout}s, last_layout={last_layout!r})"
            )
        await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _can_click(app, button):
    if not button.display or button.disabled or button.has_class("-active"):
        return False
    region = button.region
    if region.width <= 1 or region.height <= 1:
        return False
    x, y = region.x + 1, region.y + 1
    if not app.screen.region.contains(x, y):
        return False
    try:
        hit, _ = app.screen.get_widget_at(x, y)
    except NoWidget:
        return False
    return hit is button


async def wait_for_button_ready(app, pilot, selector="#run", *, timeout=DEFAULT_TIMEOUT):
    button = app.screen.query_one(selector)
    await wait_for_layout_to_settle(
        pilot, (button,), lambda: _can_click(app, button), timeout=timeout
    )


async def wait_for_export_confirmation_layout(app, pilot):
    button = app.query_one("#confirm-export")
    await wait_for_layout_to_settle(
        pilot, (button, app.query_one("#form-panel")), lambda: _can_click(app, button)
    )


async def wait_for_export_to_finish(app, pilot, completion_condition):
    # 初始 idle 不等於已完成：呼叫端必須提供本次操作的完成證據。
    await wait_for_ui_state(
        pilot,
        lambda: not app._operation_in_progress and completion_condition(),
        "export operation did not finish",
        timeout=OPERATION_TIMEOUT,
    )
