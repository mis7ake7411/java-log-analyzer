import asyncio

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp
from tui_waits import wait_for_layout_to_settle


LAYOUT_CLASS_NAMES = frozenset({"compact", "wide"})
LAYOUT_WIDGET_IDS = (
    "shell",
    "hero",
    "content",
    "form-panel",
    "form-actions",
    "hero-title-row",
)


def _layout_mode_snapshot(widgets):
    return {
        widget.id: frozenset(widget.classes & LAYOUT_CLASS_NAMES)
        for widget in widgets
    }


def _layout_is_ready(app, widgets, *, mode, short):
    mode_class = "compact" if mode == "compact" else "wide"
    if not all(widget.has_class(mode_class) for widget in widgets):
        return False

    shell = app.query_one("#shell")
    content = app.query_one("#content")
    if shell.has_class("short") is not short or content.has_class("short") is not short:
        return False

    form_panel = app.query_one("#form-panel")
    output_panel = app.query_one("#output-panel")
    if mode == "wide":
        return (
            form_panel.region.width == output_panel.region.width
            and output_panel.region.x > form_panel.region.x + form_panel.region.width
        )
    return output_panel.region.y > form_panel.region.y


def _assert_consistent_style_passes(style_passes):
    assert style_passes, "resize did not apply updated styles"
    for snapshot in style_passes:
        assert all(len(classes) == 1 for classes in snapshot.values()), snapshot
        assert len(set(snapshot.values())) == 1, snapshot


class _LayoutSyncCheck:
    def __init__(self, monkeypatch, app, pilot):
        self.app = app
        self.pilot = pilot
        self.widgets = tuple(
            app.query_one(f"#{widget_id}") for widget_id in LAYOUT_WIDGET_IDS
        )
        self.geometry_widgets = (
            app.query_one("#form-panel"),
            app.query_one("#output-panel"),
        )
        self.tracked_widgets = (*self.widgets, *self.geometry_widgets)
        self.style_passes = []
        self.capture = {"enabled": False}
        original_update_styles = app.update_styles

        def observe_style_pass(node, **kwargs):
            if self.capture["enabled"]:
                self.style_passes.append(_layout_mode_snapshot(self.widgets))
            return original_update_styles(node, **kwargs)

        monkeypatch.setattr(app, "update_styles", observe_style_pass)

    async def wait_until_ready(self, *, mode, short):
        await wait_for_layout_to_settle(
            self.pilot,
            self.tracked_widgets,
            lambda: _layout_is_ready(
                self.app, self.widgets, mode=mode, short=short
            ),
        )

    def add_marker_classes(self):
        for widget in self.widgets:
            widget.add_class("layout-sync-marker")

    async def resize_and_check(self, width, height, mode, short):
        self.style_passes.clear()
        self.capture["enabled"] = True
        try:
            await self.pilot.resize_terminal(width, height)
            await self.wait_until_ready(mode=mode, short=short)
        finally:
            self.capture["enabled"] = False

        _assert_consistent_style_passes(self.style_passes)
        assert all(widget.has_class("layout-sync-marker") for widget in self.widgets)

    def sync_without_style_passes(self, width, height):
        self.style_passes.clear()
        self.capture["enabled"] = True
        try:
            self.app._sync_layout(width, height)
        finally:
            self.capture["enabled"] = False
        assert not self.style_passes


async def _run_layout_sync_check(monkeypatch):
    app = LogAnalyzerApp()
    async with app.run_test(size=(240, 44)) as pilot:
        check = _LayoutSyncCheck(monkeypatch, app, pilot)
        await check.wait_until_ready(mode="wide", short=False)
        check.add_marker_classes()

        for width, height, mode, short in (
            (80, 32, "compact", True),
            (240, 32, "wide", True),
            (240, 40, "wide", False),
            (240, 32, "wide", True),
        ):
            await check.resize_and_check(width, height, mode, short)

        check.sync_without_style_passes(260, 32)
        snapshot_before_zero_height = _layout_mode_snapshot(check.widgets)
        check.sync_without_style_passes(80, 0)
        assert _layout_mode_snapshot(check.widgets) == snapshot_before_zero_height
        assert all(
            widget.has_class("layout-sync-marker") for widget in check.widgets
        )


def test_layout_sync_applies_mode_classes_atomically_and_reflows_after_resize(monkeypatch):
    """Resize applies one complete mode per style pass and settles in the requested geometry."""
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)
    asyncio.run(_run_layout_sync_check(monkeypatch))
