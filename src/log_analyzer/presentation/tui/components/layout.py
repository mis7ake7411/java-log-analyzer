from __future__ import annotations
from textual.containers import Container
from textual.events import Resize
from textual.widgets import RichLog


class _ResponsiveAdvancedSettings(Container):
    def on_resize(self, event: Resize) -> None:
        app = self.app
        if getattr(app, "_advanced_settings_visible", False):
            app.call_after_refresh(app._sync_advanced_layout)


class _DashboardResultLog(RichLog):
    """Re-render a completed dashboard after its viewport has been resized."""

    def on_resize(self, event: Resize) -> None:
        super().on_resize(event)
        app = self.app
        if getattr(app, "_last_result", None) is not None:
            app.call_after_refresh(app._refresh_dashboard_after_resize)
