"""TUI 測試的完成條件；不重試操作、不改寫畫面，也不取代業務斷言。"""

import asyncio
from collections.abc import Callable, Iterable
from unittest.mock import patch

from textual.errors import NoWidget
from textual.events import Key


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
    app = pilot.app
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async def wait_for_stable_state():
        while True:
            while not predicate():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise AssertionError(f"{message} (timeout={timeout}s)")
                await asyncio.sleep(min(POLL_INTERVAL, remaining))

            await _wait_for_refresh(app)
            if predicate():
                return

    task = asyncio.create_task(wait_for_stable_state())
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            raise AssertionError(f"{message} (timeout={timeout}s)")
        await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def press_tab_and_wait_for_focus(pilot, *, timeout=DEFAULT_TIMEOUT):
    """送出一次 Tab 鍵事件，並等待 Textual 完成焦點切換。"""
    app = pilot.app
    previous = app.focused
    event = Key("tab", "\t")
    event.set_sender(app)
    if not app.post_message(event):
        raise RuntimeError("application stopped before Tab event was scheduled")

    await wait_for_ui_state(
        pilot,
        lambda: app.focused is not previous,
        "focus did not change after Tab",
        timeout=timeout,
    )
    return app.focused


async def set_path_input_and_wait(pilot, path_input, value):
    """等前置路徑的 Changed 事件完成，避免晚到的預覽覆蓋下一次貼上結果。"""
    app = pilot.app
    # wraps 保留真正的預覽與事件處理；只觀察指定路徑已交給預覽處理。
    with patch.object(app, "update_path_preview", wraps=app.update_path_preview) as preview:
        path_input.value = value
        await wait_for_ui_state(
            pilot,
            lambda: any(call.args and call.args[0] == value for call in preview.call_args_list),
            f"initial path preview did not finish for {path_input.id}: {value}",
        )


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


async def _wait_for_refresh(app) -> None:
    """等候 Textual 處理完目前訊息並完成畫面刷新。"""
    refreshed = asyncio.Event()
    if not app.call_after_refresh(refreshed.set):
        raise RuntimeError("application stopped before layout refresh completed")
    await refreshed.wait()


async def wait_for_layout_to_settle(
    pilot, widgets: Iterable, ready=None, *, timeout=DEFAULT_TIMEOUT
):
    widgets = tuple(widgets)
    app = pilot.app
    last_layout = None

    async def settle():
        nonlocal last_layout
        while True:
            await _wait_for_refresh(app)
            await app.animator.wait_until_complete()
            await _wait_for_refresh(app)
            layout = _layout_snapshot(widgets)
            is_ready = ready() if ready is not None else all(
                any(not node.display for node in widget.ancestors_with_self)
                or (widget.region.width > 0 and widget.region.height > 0)
                for widget in widgets
            )
            if is_ready and layout == last_layout:
                return
            last_layout = layout if is_ready else None

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
    if region.width <= 0 or region.height <= 0:
        return False
    x = region.x + min(1, region.width - 1)
    y = region.y + min(1, region.height - 1)
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
