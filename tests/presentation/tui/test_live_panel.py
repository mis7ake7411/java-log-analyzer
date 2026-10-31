import asyncio
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Button, Checkbox, OptionList, Static, Switch

from tui_waits import wait_for_layout_to_settle, wait_for_ui_state
from log_analyzer.presentation.tui.components.live_panel import LivePanel


_LIVE_CSS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "log_analyzer"
    / "tcss"
    / "tui.live.tcss"
)


class _FakeSession:
    def __init__(self, events=(), *, state="paused"):
        self.events = list(events)
        self.state = state
        self.completed = len(self.events)
        self.matched = len(self.events)
        self.received_bytes = 1234
        self.backlog = 56
        self.pending = None
        self.has_pending = False
        self.gaps = []
        self.error = None
        self.last_read_at = None
        self.level_counts = Counter()
        self.page_calls = []

    def iter_page(self, start, size):
        self.page_calls.append((start, size))
        return iter(self.events[start : start + size])

    def step(self, *_args, **_kwargs):
        raise AssertionError("LivePanel must not advance its session")

    def end(self, *_args, **_kwargs):
        raise AssertionError("LivePanel must not finish its session")

    def snapshot(self, *_args, **_kwargs):
        raise AssertionError("LivePanel must not request a session snapshot")


def _events(count, prefix="event", generation=0):
    return [
        {
            "sequence": sequence,
            "generation": generation,
            "timestamp": "2026-10-31 12:00:00.000",
            "level": "ERROR",
            "message": f"{prefix}-{sequence}",
            "filename": f"{prefix}.log",
            "line_num": sequence,
            "raw_text": f"RAW:{prefix}-{sequence}\n",
        }
        for sequence in range(1, count + 1)
    ]


class _LivePanelApp(App):
    CSS_PATH = str(_LIVE_CSS)
    CSS = """
    Screen { layout: vertical; }
    #live-host { width: 1fr; height: 1fr; min-width: 0; min-height: 0; }
    #live-host.narrow { width: 40; height: 20; }
    """

    def __init__(self, *, narrow=False):
        super().__init__()
        self.narrow = narrow
        self.requests = []

    def compose(self) -> ComposeResult:
        classes = "narrow" if self.narrow else ""
        with Container(id="live-host", classes=classes):
            yield LivePanel(id="live-panel")

    def on_live_panel_toggle_tracking(self, event):
        self.requests.append(("toggle", event.enabled))

    def on_live_panel_rebuild_requested(self, _event):
        self.requests.append(("rebuild",))

    def on_live_panel_summary_requested(self, _event):
        self.requests.append(("summary",))

    def on_live_panel_stop_requested(self, _event):
        self.requests.append(("stop",))

    def on_live_panel_open_event(self, event):
        self.requests.append(("open", event.event))


async def _settle_panel(app, pilot):
    panel = app.query_one(LivePanel)
    widgets = (
        panel,
        app.query_one("#live-switch", Switch),
        app.query_one("#live-rebuild", Button),
        app.query_one("#live-summary", Button),
        app.query_one("#live-stop", Button),
        app.query_one("#live-follow", Checkbox),
        app.query_one("#live-previous", Button),
        app.query_one("#live-next", Button),
        app.query_one("#live-events", OptionList),
    )
    await wait_for_layout_to_settle(pilot, widgets)
    return panel


def _regions_overlap(left, right):
    return (
        left.x < right.x + right.width
        and right.x < left.x + left.width
        and left.y < right.y + right.height
        and right.y < left.y + left.height
    )


def test_switch_and_action_buttons_emit_messages_and_empty_panel_can_start():
    async def run_check():
        app = _LivePanelApp()
        async with app.run_test(size=(80, 24)) as pilot:
            panel = await _settle_panel(app, pilot)
            assert app.query_one("#live-rebuild", Button).disabled
            assert app.query_one("#live-summary", Button).disabled
            assert app.query_one("#live-stop", Button).disabled

            switch = app.query_one("#live-switch", Switch)
            await pilot.click(switch)
            await wait_for_ui_state(
                pilot,
                lambda: app.requests == [("toggle", True)],
                "start toggle was not sent",
            )

            panel.set_session(_FakeSession(_events(1), state="paused"))
            assert not app.query_one("#live-switch", Switch).value
            assert app.requests == [("toggle", True)]
            panel.set_session(_FakeSession(_events(1), state="tracking"))
            assert app.query_one("#live-switch", Switch).value
            assert app.requests == [("toggle", True)]
            await pilot.click("#live-rebuild")
            await pilot.click("#live-summary")
            await pilot.click("#live-stop")
            switch = app.query_one("#live-switch", Switch)
            await pilot.click(switch)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    app.requests[-4:]
                    == [
                        ("rebuild",),
                        ("summary",),
                        ("stop",),
                        ("toggle", False),
                    ]
                ),
                "live panel controls did not emit their requests",
            )

    asyncio.run(run_check())


def test_click_and_enter_open_captured_raw_event_without_rendering_raw_text():
    async def run_check():
        app = _LivePanelApp()
        event = _events(1, prefix="captured", generation=7)[0]
        async with app.run_test(size=(80, 24)) as pilot:
            panel = await _settle_panel(app, pilot)
            panel.set_session(_FakeSession([event], state="tracking"))
            options = app.query_one("#live-events", OptionList)
            await wait_for_layout_to_settle(pilot, (options,))
            assert "RAW:" not in str(options.options[0].prompt)
            assert "captured.log" in str(options.options[0].prompt)
            assert "g7:L1" in str(options.options[0].prompt)

            assert await pilot.click(options, offset=(2, 1))
            await wait_for_ui_state(
                pilot,
                lambda: len(app.requests) == 1,
                "click did not open the selected event",
            )
            assert app.requests[0] == ("open", event)

            options.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: len(app.requests) == 2,
                "Enter did not open the highlighted event",
            )
            assert app.requests[1] == ("open", event)

    asyncio.run(run_check())


def test_event_option_uses_bounded_single_line_summary_and_open_keeps_full_payload():
    async def run_check():
        app = _LivePanelApp()
        message = "first line\nsecond line " + ("detail " * 60)
        event = _events(1, prefix="long-message")[0] | {
            "message": message,
            "raw_text": f"FULL:{message}\nstack frame\n",
        }
        async with app.run_test(size=(80, 24)) as pilot:
            panel = await _settle_panel(app, pilot)
            panel.set_session(_FakeSession([event], state="tracking"))
            options = app.query_one("#live-events", OptionList)
            prompt_lines = str(options.options[0].prompt).splitlines()

            assert len(prompt_lines) == 2
            assert len(prompt_lines[1]) <= 123
            assert "first line second line" in prompt_lines[1]
            assert "FULL:" not in prompt_lines[1]

            options.focus()
            await pilot.press("enter")
            await wait_for_ui_state(
                pilot,
                lambda: len(app.requests) == 1,
                "Enter did not open the full event payload",
            )
            assert app.requests[0] == ("open", event)

    asyncio.run(run_check())


def test_following_and_manual_pages_preserve_read_position_and_replace_session_data():
    async def run_check():
        app = _LivePanelApp()
        old_session = _FakeSession(_events(205, prefix="old"), state="tracking")
        async with app.run_test(size=(100, 30)) as pilot:
            panel = await _settle_panel(app, pilot)
            panel.set_session(old_session)
            options = app.query_one("#live-events", OptionList)
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#live-page").render().plain == "3 / 3",
                "initial follow did not move to the latest page",
            )
            assert len(options.options) == 5
            assert "old-205" in str(options.options[-1].prompt)
            assert options.highlighted == 4

            options.focus()
            await pilot.press("up")
            await wait_for_ui_state(
                pilot,
                lambda: not app.query_one("#live-follow", Checkbox).value,
                "reading upward did not turn follow mode off",
            )
            assert options.highlighted == 3

            previous = app.query_one("#live-previous", Button)
            await pilot.click(previous)
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#live-page").render().plain == "2 / 3",
                "previous page did not update the page index",
            )
            assert not app.query_one("#live-follow", Checkbox).value
            assert len(options.options) == 100
            options.focus()
            preserved_highlight = options.highlighted
            old_session.events.append(_events(1, prefix="old")[0] | {"sequence": 206})
            old_session.matched = 206
            old_session.completed = 206
            panel.refresh_session()
            await wait_for_ui_state(
                pilot,
                lambda: (
                    "新增：1 筆"
                    in app.query_one("#live-new-events", Static).render().plain
                ),
                "unfollowed append was not reported",
            )
            assert app.query_one("#live-page").render().plain == "2 / 3"
            assert options.highlighted == preserved_highlight
            assert app.focused is options
            assert "old-101" in str(options.options[0].prompt)
            assert "old-200" in str(options.options[-1].prompt)

            await pilot.click("#live-next")
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#live-page").render().plain == "3 / 3",
                "next page did not update the page index",
            )
            assert "#206" in str(options.options[-1].prompt)

            replacement = _FakeSession(_events(3, prefix="new", generation=1))
            panel.set_session(replacement)
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#live-page").render().plain == "1 / 1",
                "new session did not reset to its own first page",
            )
            assert len(options.options) == 3
            assert all("new-" in str(option.prompt) for option in options.options)
            assert all("old-" not in str(option.prompt) for option in options.options)

            panel.set_session(None)
            assert app.query_one("#live-page").render().plain == "1 / 1"
            assert len(options.options) == 0

    asyncio.run(run_check())


def test_follow_latest_moves_on_append_but_set_busy_keeps_reading_enabled():
    async def run_check():
        app = _LivePanelApp()
        session = _FakeSession(_events(200), state="tracking")
        async with app.run_test(size=(100, 30)) as pilot:
            panel = await _settle_panel(app, pilot)
            panel.set_session(session)
            options = app.query_one("#live-events", OptionList)
            assert app.query_one("#live-page").render().plain == "2 / 2"
            options.focus()

            session.events.append(_events(1)[0] | {"sequence": 201})
            session.matched = 201
            session.completed = 201
            panel.refresh_session()
            await wait_for_ui_state(
                pilot,
                lambda: app.query_one("#live-page").render().plain == "3 / 3",
                "follow mode did not move to the appended final page",
            )
            assert len(options.options) == 1
            assert options.highlighted == 0
            assert "#201" in str(options.options[0].prompt)
            assert app.focused is options
            assert app.query_one("#live-follow", Checkbox).value
            assert (
                app.query_one("#live-new-events", Static).render().plain == "跟隨最新"
            )

            previous_disabled = app.query_one("#live-previous", Button).disabled
            next_disabled = app.query_one("#live-next", Button).disabled
            panel.set_busy(True)
            assert app.query_one("#live-switch", Switch).disabled
            assert app.query_one("#live-rebuild", Button).disabled
            assert app.query_one("#live-summary", Button).disabled
            assert app.query_one("#live-stop", Button).disabled
            assert not app.query_one("#live-follow", Checkbox).disabled
            assert not options.disabled
            assert app.query_one("#live-previous", Button).disabled is previous_disabled
            assert app.query_one("#live-next", Button).disabled is next_disabled
            panel.set_busy(False)

    asyncio.run(run_check())


def test_follow_latest_scrolls_to_latest_event_within_page():
    async def run_check():
        app = _LivePanelApp()
        session = _FakeSession(_events(80), state="tracking")
        async with app.run_test(size=(80, 24)) as pilot:
            panel = await _settle_panel(app, pilot)
            panel.set_session(session)
            options = app.query_one("#live-events", OptionList)
            await wait_for_layout_to_settle(pilot, (options,))

            assert options.highlighted == 79
            assert options.scroll_y == options.max_scroll_y
            assert "#80" in str(options.options[-1].prompt)

    asyncio.run(run_check())


def test_unfollowed_refresh_and_append_preserve_option_list_scroll_and_focus():
    async def run_check():
        app = _LivePanelApp()
        session = _FakeSession(_events(99), state="tracking")
        async with app.run_test(size=(80, 24)) as pilot:
            panel = await _settle_panel(app, pilot)
            panel.set_session(session)
            options = app.query_one("#live-events", OptionList)
            await pilot.click("#live-follow")
            await wait_for_ui_state(
                pilot,
                lambda: not app.query_one("#live-follow", Checkbox).value,
                "follow mode did not turn off",
            )
            options.focus()
            await wait_for_layout_to_settle(pilot, (options,))
            assert options.max_scroll_y > 0
            options.scroll_to(
                y=options.max_scroll_y // 2,
                animate=False,
                immediate=True,
            )
            scroll_y = options.scroll_y
            highlighted = options.highlighted
            first_option = options.options[0]

            panel.refresh_session()
            await wait_for_layout_to_settle(pilot, (options,))
            assert options.options[0] is first_option
            assert options.scroll_y == scroll_y
            assert options.highlighted == highlighted
            assert app.focused is options

            session.events.append(_events(1)[0] | {"sequence": 100})
            session.matched = 100
            session.completed = 100
            panel.refresh_session()
            await wait_for_layout_to_settle(pilot, (options,))
            assert len(options.options) == 100
            assert "#100" in str(options.options[-1].prompt)
            assert options.options[0] is first_option
            assert options.scroll_y == scroll_y
            assert options.highlighted == highlighted
            assert app.focused is options

    asyncio.run(run_check())


def test_status_shows_all_session_counters_pending_gaps_and_errors():
    async def run_check():
        app = _LivePanelApp()
        session = _FakeSession(_events(2), state="tracking")
        session.completed = 9
        session.matched = 2
        session.has_pending = True
        session.pending = {"message": "partial"}
        session.gaps = ["copytruncate detected"]
        session.error = "disk unavailable"
        async with app.run_test(size=(80, 24)) as pilot:
            panel = await _settle_panel(app, pilot)
            panel.set_session(session)
            status = app.query_one("#live-status", Static)
            await wait_for_ui_state(
                pilot,
                lambda: "disk unavailable" in status.render().plain,
                "session error status was not rendered",
            )
            text = status.render().plain
            assert "追蹤中" in text
            assert "完成 9" in text and "命中 2" in text
            assert "接收 1234 B" in text and "待讀 56 B" in text
            assert "待處理：有" in text
            assert "copytruncate detected" in text
            assert "disk unavailable" in text

            expected = {
                "tracking": "追蹤中",
                "paused": "已暫停",
                "interrupted": "來源中斷",
                "error": "錯誤",
                "stopped": "已結束",
            }
            for state, label in expected.items():
                session.state = state
                panel.refresh_session()
                assert label in status.render().plain
            assert session.page_calls

    asyncio.run(run_check())


def test_status_shows_last_read_and_per_level_counts_with_explicit_empty_values():
    async def run_check():
        app = _LivePanelApp()
        session = _FakeSession([], state="paused")
        async with app.run_test(size=(80, 24)) as pilot:
            panel = await _settle_panel(app, pilot)
            status = app.query_one("#live-status", Static)

            await wait_for_ui_state(
                pilot,
                lambda: "Level：尚無" in status.render().plain,
                "initial empty status was not rendered",
            )
            empty = status.render().plain
            assert "最後讀取：尚無" in empty
            assert "Level：尚無" in empty

            panel.set_session(session)
            await wait_for_ui_state(
                pilot,
                lambda: "Level：尚無" in status.render().plain,
                "empty session did not expose empty read and level stats",
            )
            empty_session = status.render().plain
            assert "最後讀取：尚無" in empty_session
            assert "ERROR=0" not in empty_session

            session.last_read_at = datetime(
                2026, 10, 31, 12, 34, 56, tzinfo=timezone.utc
            )
            session.level_counts = Counter({"ERROR": 3, "WARN": 2, "INFO": 1})
            panel.refresh_session()
            await wait_for_ui_state(
                pilot,
                lambda: "TRACE=0" in status.render().plain,
                "last read time or per-level counts were not rendered",
            )
            text = status.render().plain
            assert "2026-10-31 12:34:56 UTC" in text
            assert "ERROR=3" in text
            assert "WARN=2" in text
            assert "INFO=1" in text
            assert "TRACE=0" in text

    asyncio.run(run_check())


@pytest.mark.parametrize(
    ("size", "narrow"),
    [((80, 24), False), ((160, 44), False), ((80, 24), True)],
)
def test_controls_stay_inside_panel_and_event_list_uses_remaining_height(size, narrow):
    async def run_check():
        app = _LivePanelApp(narrow=narrow)
        async with app.run_test(size=size) as pilot:
            panel = await _settle_panel(app, pilot)
            session = _FakeSession(_events(80), state="tracking")
            session.last_read_at = datetime(
                2026, 10, 31, 12, 34, 56, tzinfo=timezone.utc
            )
            session.level_counts = Counter(
                {"ERROR": 3, "WARN": 2, "INFO": 1, "DEBUG": 0, "TRACE": 0}
            )
            session.gaps = ["gap " + "g" * 400 for _ in range(20)]
            session.error = "error " + "e" * 400
            panel.set_session(session)
            controls = [
                app.query_one(selector)
                for selector in (
                    "#live-switch",
                    "#live-rebuild",
                    "#live-rebuild-warning",
                    "#live-summary",
                    "#live-stop",
                    "#live-follow",
                    "#live-previous",
                    "#live-next",
                    "#live-page",
                    "#live-new-events",
                    "#live-status",
                    "#live-events",
                )
            ]
            await wait_for_layout_to_settle(pilot, (panel, *controls))
            content = panel.content_region
            right = content.x + content.width
            bottom = content.y + content.height
            assert panel.region.width >= (36 if narrow else size[0] - 2)
            assert app.query_one("#live-events", OptionList).region.height > 0
            assert app.query_one("#live-events", OptionList).max_scroll_y > 0
            assert all(control.display for control in controls)
            warning = app.query_one("#live-rebuild-warning", Static)
            assert (
                warning.render().plain.replace("\n", "")
                == "從目前來源重建，成功後取代本次追蹤結果"
            )
            assert warning.region.height >= (2 if narrow else 1)
            status = app.query_one("#live-status", Static).render().plain
            assert "2026-10-31 12:34:56 UTC" in status
            assert "ERROR=3" in status and "WARN=2" in status
            assert "缺口 20" in status and "錯誤：" in status
            assert len(status.splitlines()) <= 7
            for selector, label in (
                ("#live-rebuild", "重新分析來源"),
                ("#live-summary", "查看追蹤摘要"),
                ("#live-stop", "結束追蹤"),
            ):
                button = app.query_one(selector, Button)
                assert str(button.label) == label
                assert button.region.width >= 2 * len(label) + 2
            out_of_bounds = [
                (
                    control.id,
                    control.region,
                    content,
                )
                for control in controls
                if not (
                    control.region.x >= content.x
                    and control.region.x + control.region.width <= right
                    and control.region.y >= content.y
                    and control.region.y + control.region.height <= bottom
                )
            ]
            assert not out_of_bounds, f"panel controls exceeded bounds: {out_of_bounds}"
            overlaps = [
                (left.id, right.id)
                for index, left in enumerate(controls)
                for right in controls[index + 1 :]
                if _regions_overlap(left.region, right.region)
            ]
            regions = {control.id: control.region for control in controls}
            assert not overlaps, (
                f"panel controls overlapped: {overlaps}; regions={regions}"
            )

    asyncio.run(run_check())
