"""驗證等待不會提前成功、吞掉例外或取消產品工作。"""

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button

from tui_waits import (
    wait_for_button_ready,
    wait_for_export_to_finish,
    wait_for_layout_to_settle,
    wait_for_scheduled_analysis,
    wait_until,
)


def test_analysis_waits_for_a_new_task_and_its_result():
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


def test_export_idle_before_start_is_not_completion():
    class Pilot:
        async def pause(self):
            await asyncio.sleep(0)

    async def check():
        app = SimpleNamespace(_operation_in_progress=False)
        report = asyncio.Event()
        waiting = asyncio.create_task(wait_for_export_to_finish(app, Pilot(), report.is_set))
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
    async def check():
        app = WaitHarness()
        async with app.run_test() as pilot:
            button = app.query_one("#run", Button)
            button.styles.animate("opacity", 0.5, duration=0.05, delay=0.05)
            await wait_for_layout_to_settle(pilot, (button,))
            assert button.styles.opacity == 0.5

    asyncio.run(check())


def test_button_wait_uses_the_active_modal_screen():
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


def test_layout_allows_children_of_a_hidden_container():
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
            await wait_for_layout_to_settle(pilot, (parent, child), timeout=0.2)
            assert child.display
            assert child.region.width == 0

    asyncio.run(check())
