"""驗證等待不會提前成功、吞掉例外或取消產品工作。"""

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button
from textual import screen as screen_module
from textual.geometry import Offset, Region, Size

import tui_waits

from tui_waits import (
    wait_for_button_ready,
    wait_for_export_to_finish,
    wait_for_layout_to_settle,
    wait_for_scheduled_analysis,
    wait_for_ui_state,
    wait_until,
)


def test_analysis_waits_for_a_new_task_and_its_result():
    """情境：分析等待應等待新 task 建立及其實際完成。"""
    async def check():
        previous = asyncio.create_task(asyncio.sleep(0))
        await previous
        app = SimpleNamespace(_scheduled_analysis_task=previous)
        gate = asyncio.Event()
        waiting = asyncio.create_task(wait_for_scheduled_analysis(app, previous))
        await asyncio.sleep(0)
        assert not waiting.done()
        app._scheduled_analysis_task = asyncio.create_task(gate.wait())
        await asyncio.sleep(0)
        assert not waiting.done()
        gate.set()
        await waiting
        assert app._scheduled_analysis_task.done()

    asyncio.run(check())


def test_analysis_timeout_does_not_cancel_the_application_task():
    """情境：分析等待逾時不應取消應用程式 task。"""
    async def check():
        gate = asyncio.Event()
        task = asyncio.create_task(gate.wait())
        app = SimpleNamespace(_scheduled_analysis_task=task)
        try:
            with pytest.raises(AssertionError, match="analysis task did not finish"):
                await wait_for_scheduled_analysis(app, timeout=0.02)
            assert not task.done()
        finally:
            gate.set()
            await task

    asyncio.run(check())


def test_waits_propagate_application_and_predicate_errors():
    """情境：等待函式應保留應用程式及判斷條件的原始例外。"""
    async def fail():
        raise TimeoutError("application failure")

    def broken_predicate():
        raise ValueError("predicate failure")

    async def check():
        app = SimpleNamespace(_scheduled_analysis_task=asyncio.create_task(fail()))
        with pytest.raises(TimeoutError, match="application failure"):
            await wait_for_scheduled_analysis(app)
        with pytest.raises(ValueError, match="predicate failure"):
            await wait_until(broken_predicate)
        with pytest.raises(AssertionError, match="never ready"):
            await wait_until(lambda: False, "never ready", timeout=0.02)

    asyncio.run(check())


def test_ui_state_wait_rechecks_after_refresh_and_waits_for_stable_completion():
    """刷新期間狀態失效後，須等狀態恢復並完成下一個刷新 barrier。"""
    async def check():
        state = {"ready": True}

        class App:
            def __init__(self):
                self.refresh_count = 0
                self.first_refresh = asyncio.Event()
                self.second_refresh_scheduled = asyncio.Event()
                self.finish_second_refresh = None

            def call_after_refresh(self, callback):
                self.refresh_count += 1
                if self.refresh_count == 1:
                    def invalidate_during_refresh():
                        state["ready"] = False
                        callback()
                        self.first_refresh.set()

                    asyncio.get_running_loop().call_soon(invalidate_during_refresh)
                elif self.refresh_count == 2:
                    self.finish_second_refresh = callback
                    self.second_refresh_scheduled.set()
                else:
                    asyncio.get_running_loop().call_soon(callback)
                return True

        class Pilot:
            def __init__(self, app):
                self.app = app

            async def pause(self):
                state["ready"] = False
                self.app.first_refresh.set()

        app = App()
        waiting = asyncio.create_task(
            wait_for_ui_state(
                Pilot(app), lambda: state["ready"], "state did not settle", timeout=0.5
            )
        )
        await asyncio.wait_for(app.first_refresh.wait(), timeout=0.2)
        assert state["ready"] is False
        assert not waiting.done()

        state["ready"] = True
        await asyncio.wait_for(app.second_refresh_scheduled.wait(), timeout=0.2)
        assert not waiting.done()
        assert app.refresh_count == 2

        app.finish_second_refresh()
        await waiting

    asyncio.run(check())


def test_ui_state_wait_times_out_on_a_stalled_refresh_without_cancelling_app_work():
    """刷新 barrier 停滯時須逾時，且不得取消獨立的產品工作。"""
    async def check():
        app = SimpleNamespace(call_after_refresh=lambda _callback: True)
        pilot = SimpleNamespace(app=app, pause=lambda: asyncio.sleep(0))
        release_work = asyncio.Event()
        app_work = asyncio.create_task(release_work.wait())

        with pytest.raises(AssertionError, match=r"refresh stalled .*timeout=0\.02s"):
            await wait_for_ui_state(
                pilot, lambda: True, "refresh stalled", timeout=0.02
            )

        assert not app_work.done()
        release_work.set()
        await app_work

    asyncio.run(check())


def test_ui_state_wait_propagates_predicate_and_refresh_scheduling_errors():
    """狀態 predicate 與刷新排程的原始例外須直接傳回。"""
    async def check():
        async def pause():
            await asyncio.sleep(0)

        def broken_predicate():
            raise ValueError("ui predicate failure")

        pilot = SimpleNamespace(
            app=SimpleNamespace(call_after_refresh=lambda _callback: True),
            pause=pause,
        )
        with pytest.raises(ValueError, match="ui predicate failure"):
            await wait_for_ui_state(pilot, broken_predicate, "unused")

        def fail_to_schedule(_callback):
            raise TimeoutError("refresh scheduling failure")

        pilot.app = SimpleNamespace(call_after_refresh=fail_to_schedule)
        with pytest.raises(TimeoutError, match="refresh scheduling failure"):
            await wait_for_ui_state(pilot, lambda: True, "unused")

    asyncio.run(check())


def test_export_idle_before_start_is_not_completion():
    """情境：匯出尚未開始前的閒置不得當成完成。"""
    class App:
        def __init__(self):
            self._operation_in_progress = False

        def call_after_refresh(self, callback):
            asyncio.get_running_loop().call_soon(callback)
            return True

    class Pilot:
        def __init__(self, app):
            self.app = app

        async def pause(self):
            await asyncio.sleep(0)

    async def check():
        app = App()
        report = asyncio.Event()
        waiting = asyncio.create_task(wait_for_export_to_finish(app, Pilot(app), report.is_set))
        await asyncio.sleep(0)
        assert not waiting.done()
        app._operation_in_progress = True
        report.set()
        await asyncio.sleep(0)
        assert not waiting.done()
        app._operation_in_progress = False
        await waiting

    asyncio.run(check())


class WaitHarness(App):
    def compose(self) -> ComposeResult:
        yield Button("Ready", id="run", disabled=True)


def test_button_wait_does_not_click_or_enable_a_disabled_button():
    """情境：等待按鈕不得自行點擊或啟用停用中的按鈕。"""
    async def check():
        app = WaitHarness()
        async with app.run_test() as pilot:
            button = app.query_one("#run", Button)
            with pytest.raises(AssertionError, match="layout did not settle"):
                await wait_for_button_ready(app, pilot, timeout=0.05)
            assert button.disabled
            button.disabled = False
            await wait_for_button_ready(app, pilot)
            assert not button.has_class("-active")

    asyncio.run(check())


def test_layout_waits_for_scheduled_animation_before_accepting_equal_regions():
    """情境：接受相同版面前應等待已排程動畫完成。"""
    async def check():
        app = WaitHarness()
        async with app.run_test() as pilot:
            button = app.query_one("#run", Button)
            button.styles.animate("opacity", 0.5, duration=0.05, delay=0.05)
            await wait_for_layout_to_settle(pilot, (button,))
            assert button.styles.opacity == 0.5

    asyncio.run(check())


def test_layout_waits_for_nested_after_refresh_reflow_without_pilot_idle_waits():
    """等待巢狀 refresh callback 完成與穩定版面，不呼叫 Pilot 的 idle 等待。"""
    class NoIdlePilot:
        def __init__(self, app):
            self.app = app

        async def pause(self):
            raise AssertionError("layout wait must not use Pilot.pause")

        async def wait_for_scheduled_animations(self):
            raise AssertionError("layout wait must use the app animator directly")

    class ReflowHarness(App):
        def compose(self) -> ComposeResult:
            yield Button("Reflow", id="run")

    async def check():
        app = ReflowHarness()
        async with app.run_test() as pilot:
            button = app.query_one("#run", Button)
            initial_pause = pilot.pause
            await initial_pause()

            def finish_reflow():
                button.styles.width = 20
                button.add_class("reflow-finished")

            def start_reflow():
                button.styles.width = 10
                app.call_after_refresh(finish_reflow)

            app.call_after_refresh(start_reflow)
            await wait_for_layout_to_settle(
                NoIdlePilot(app),
                (button,),
                lambda: button.has_class("reflow-finished")
                and button.region.width == 20,
            )
            assert button.region.width == 20

    asyncio.run(check())


def test_layout_refresh_barrier_propagates_scheduling_errors(monkeypatch):
    """刷新 barrier 的排程錯誤須原樣傳回呼叫端。"""
    class LayoutHarness(App):
        def compose(self) -> ComposeResult:
            yield Button("Ready", id="run")

    async def check():
        app = LayoutHarness()
        async with app.run_test() as pilot:
            await pilot.pause()

            def fail_to_schedule(_callback, *args, **kwargs):
                raise RuntimeError("refresh scheduling failure")

            monkeypatch.setattr(app, "call_after_refresh", fail_to_schedule)
            with pytest.raises(RuntimeError, match="refresh scheduling failure"):
                await wait_for_layout_to_settle(pilot, (app.query_one("#run"),))

    asyncio.run(check())


def test_layout_refresh_barrier_timeout_keeps_layout_diagnostic(monkeypatch):
    """刷新 callback 未完成時須逾時，並保留最後版面診斷。"""
    class LayoutHarness(App):
        def compose(self) -> ComposeResult:
            yield Button("Ready", id="run")

    async def check():
        app = LayoutHarness()
        async with app.run_test() as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "call_after_refresh", lambda *_args, **_kwargs: True)

            with pytest.raises(AssertionError, match=r"layout did not settle .*last_layout="):
                await wait_for_layout_to_settle(pilot, (app.query_one("#run"),), timeout=0.05)

    asyncio.run(check())


def test_button_wait_uses_the_active_modal_screen():
    """情境：按鈕等待應以目前活動的彈出視窗為準。"""
    class Dialog(ModalScreen):
        def compose(self) -> ComposeResult:
            yield Button("Next", id="next")

    async def check():
        app = WaitHarness()
        async with app.run_test() as pilot:
            await app.push_screen(Dialog())
            await wait_for_button_ready(app, pilot, "#next")
            assert app.screen.query_one("#next").region.width > 0

    asyncio.run(check())


@pytest.mark.parametrize("refresh_hz", [60, 8], ids=["normal-refresh", "slow-refresh"])
def test_layout_allows_children_of_a_hidden_container(monkeypatch, refresh_hz):
    """情境：隱藏容器中的子元件應允許沒有可見尺寸。"""
    # 真實刷新受 frame timer 排程；低刷新率重現 200 ms 速度假設的失敗。
    monkeypatch.setattr(screen_module, "UPDATE_PERIOD", 1 / refresh_hz)

    class HiddenHarness(App):
        def compose(self) -> ComposeResult:
            with Container(id="hidden"):
                yield Button("Hidden", id="child")

    async def check():
        app = HiddenHarness()
        async with app.run_test() as pilot:
            parent = app.query_one("#hidden")
            child = app.query_one("#child")
            parent.display = False
            # 驗證隱藏後的完成狀態，不額外要求主機在 200 ms 內刷新。
            await wait_for_layout_to_settle(pilot, (parent, child))
            assert not parent.display
            assert child.display
            assert child.region.width == 0

    asyncio.run(check())


def test_hidden_layout_still_requires_a_second_refreshed_snapshot(monkeypatch):
    """隱藏子元件可為零尺寸，但不能跳過後續刷新與穩定性確認。"""
    async def check():
        parent = SimpleNamespace(
            region=Region(), display=False, classes=set(), content_size=Size(),
            scroll_offset=Offset(), max_scroll_y=0,
        )
        child = SimpleNamespace(
            region=Region(), display=True, classes=set(), content_size=Size(),
            scroll_offset=Offset(), max_scroll_y=2,
        )
        parent.ancestors_with_self = (parent,)
        child.ancestors_with_self = (child, parent)
        blocked_refresh = asyncio.Event()
        pending_callbacks = []
        snapshot_count = 0
        release_refresh = False
        original_snapshot = tui_waits._layout_snapshot

        def observe_snapshot(widgets):
            nonlocal snapshot_count
            snapshot_count += 1
            return original_snapshot(widgets)

        def schedule_refresh(callback):
            if snapshot_count == 1 and not release_refresh:
                pending_callbacks.append(callback)
                blocked_refresh.set()
            else:
                asyncio.get_running_loop().call_soon(callback)
            return True

        async def no_animations():
            return None

        monkeypatch.setattr(tui_waits, "_layout_snapshot", observe_snapshot)
        pilot = SimpleNamespace(app=SimpleNamespace(
            call_after_refresh=schedule_refresh,
            animator=SimpleNamespace(wait_until_complete=no_animations),
        ))
        waiting = asyncio.create_task(wait_for_layout_to_settle(pilot, (parent, child)))
        try:
            await asyncio.wait_for(blocked_refresh.wait(), timeout=tui_waits.DEFAULT_TIMEOUT)
            assert snapshot_count == 1
            assert not waiting.done()
            release_refresh = True
            for callback in pending_callbacks:
                callback()
            await waiting
            assert snapshot_count == 2
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)

    asyncio.run(check())
