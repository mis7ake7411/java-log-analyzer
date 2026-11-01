"""立即完成的追蹤操作也必須先進入忙碌狀態，再發布完成結果。"""

import asyncio

from log_analyzer.presentation.tui.controllers.live import LiveWorkflowMixin


def test_eager_operation_completion_is_not_overwritten_by_starting_notice():
    """條件不同而立即拒絕接續時，不留下過期交接提示或工作指標。"""
    class Harness(LiveWorkflowMixin):
        is_running = True
        _live_operation_task = None
        _live_operation_name = None

        def __init__(self):
            self.notices = []
            self.busy = False

        def query_one(self, *_args):
            return self

        def set_busy(self, value):
            self.busy = value

        def _set_analysis_cancel_button(self, _value):
            pass

        def _sync_live_controls(self):
            pass

        def _set_live_notice(self, message):
            self.notices.append(message)

    async def scenario():
        asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)
        app = Harness()

        async def refuse_resume():
            app._set_live_notice("條件已變更，請重新分析")

        assert app._schedule_live_operation("追蹤狀態交接", refuse_resume)
        task = app._live_operation_task
        if task is not None:
            await task
        assert app.notices[-1] == "條件已變更，請重新分析"
        assert not app.busy
        assert app._live_operation_task is None
        assert app._live_operation_name is None

    asyncio.run(scenario())
