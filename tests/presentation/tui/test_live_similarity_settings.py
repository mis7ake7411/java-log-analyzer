import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Checkbox, Input, Static

from tui_waits import wait_for_layout_to_settle, wait_for_ui_state
from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.conditions import LiveAnalysisRequest
from log_analyzer.presentation.tui.components.live_similarity import (
    LiveSimilaritySettings,
)


_SIMILARITY_CSS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "log_analyzer"
    / "tcss"
    / "tui.live-similarity.tcss"
)


class _LiveSimilaritySettingsApp(App):
    CSS_PATH = str(_SIMILARITY_CSS)
    CSS = """
    Screen { layout: vertical; }
    #similarity-host {
        width: 1fr;
        height: 1fr;
        min-width: 0;
        min-height: 0;
    }
    #similarity-host.narrow { width: 40; }
    """

    def __init__(self, *, narrow: bool = False) -> None:
        super().__init__()
        self.narrow = narrow
        self.changes = 0

    def compose(self) -> ComposeResult:
        classes = "narrow" if self.narrow else ""
        with VerticalScroll(id="similarity-host", classes=classes):
            yield LiveSimilaritySettings()

    def on_live_similarity_settings_changed(self, _event) -> None:
        self.changes += 1


async def _settle(app, pilot):
    settings = app.query_one(LiveSimilaritySettings)
    widgets = (
        settings,
        app.query_one("#live-similarity-enabled", Checkbox),
    )
    await wait_for_layout_to_settle(pilot, widgets)
    return settings


def _base_request() -> AnalysisRequest:
    return AnalysisRequest(
        path="logs",
        start_dt=datetime(2026, 10, 1, tzinfo=timezone.utc),
        end_dt=datetime(2026, 10, 2, tzinfo=timezone.utc),
        keyword="database",
        ignore_case=True,
        sort_by="level",
        log_pattern="%msg",
        levels=("ERROR", "WARN"),
        selected_files=("app.log",),
    )


def test_similarity_settings_start_disabled_collapsed_and_leave_base_request_unchanged():
    async def run_check():
        app = _LiveSimilaritySettingsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            settings = await _settle(app, pilot)
            enabled = app.query_one("#live-similarity-enabled", Checkbox)
            details = app.query_one("#live-similarity-details")
            assert str(enabled.label) == "相似訊息"
            assert not enabled.value
            assert not details.display
            assert settings.get_identity() is None
            base = _base_request()
            assert settings.as_request(base) is base
            assert app.changes == 0

    asyncio.run(run_check())


def test_checkbox_and_inputs_build_live_request_with_all_base_fields():
    async def run_check():
        app = _LiveSimilaritySettingsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            settings = await _settle(app, pilot)
            enabled = app.query_one("#live-similarity-enabled", Checkbox)
            await pilot.click(enabled)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    app.changes == 1
                    and app.query_one("#live-similarity-details").display
                ),
                "enabling similarity did not reveal its fields",
            )

            reference = app.query_one("#live-similarity-reference", Input)
            threshold = app.query_one("#live-similarity-threshold", Input)
            await wait_for_layout_to_settle(pilot, (reference, threshold))
            reference.focus()
            await wait_for_ui_state(
                pilot, lambda: app.focused is reference, "reference field did not focus"
            )
            await pilot.press(
                *(
                    "space" if character == " " else character
                    for character in "Database unavailable"
                )
            )
            assert reference.value == "Database unavailable", (
                reference.value,
                app.focused,
            )
            threshold.value = ""
            await wait_for_ui_state(
                pilot, lambda: threshold.value == "", "threshold field did not clear"
            )
            threshold.focus()
            await wait_for_ui_state(
                pilot, lambda: app.focused is threshold, "threshold field did not focus"
            )
            await pilot.press(*"92.5")
            assert threshold.value == "92.5", (threshold.value, app.focused)
            await wait_for_ui_state(
                pilot,
                lambda: (
                    reference.value == "Database unavailable"
                    and threshold.value == "92.5"
                ),
                "reference and threshold edits were not applied",
            )
            assert settings.get_identity() == ("Database unavailable", 92.5)
            assert app.changes > 1

            base = _base_request()
            request = settings.as_request(base)
            assert isinstance(request, LiveAnalysisRequest)
            assert request == LiveAnalysisRequest(
                path=base.path,
                start_dt=base.start_dt,
                end_dt=base.end_dt,
                keyword=base.keyword,
                ignore_case=base.ignore_case,
                sort_by=base.sort_by,
                log_pattern=base.log_pattern,
                levels=base.levels,
                selected_files=base.selected_files,
                similar_to="Database unavailable",
                similarity_threshold=92.5,
            )
            assert base == _base_request()

    asyncio.run(run_check())


def test_set_reference_enables_only_settings_and_lock_preserves_values():
    async def run_check():
        app = _LiveSimilaritySettingsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            settings = await _settle(app, pilot)
            settings.set_reference("Timeout while waiting")
            await wait_for_ui_state(
                pilot,
                lambda: (
                    app.changes == 1
                    and app.query_one("#live-similarity-enabled", Checkbox).value
                ),
                "set_reference did not populate and enable similarity settings",
            )
            reference = app.query_one("#live-similarity-reference", Input)
            threshold = app.query_one("#live-similarity-threshold", Input)
            assert reference.value == "Timeout while waiting"
            assert threshold.value == "80"
            assert settings.get_identity() == ("Timeout while waiting", 80.0)
            assert app.changes == 1

            settings.set_locked(True)
            assert all(
                app.query_one(selector, widget_type).disabled
                for selector, widget_type in (
                    ("#live-similarity-enabled", Checkbox),
                    ("#live-similarity-reference", Input),
                    ("#live-similarity-threshold", Input),
                )
            )
            settings.set_locked(False)
            assert not app.query_one("#live-similarity-enabled", Checkbox).disabled
            assert not reference.disabled
            assert not threshold.disabled
            assert reference.value == "Timeout while waiting"
            assert settings.get_identity() == ("Timeout while waiting", 80.0)

            await pilot.click(app.query_one("#live-similarity-enabled", Checkbox))
            await wait_for_ui_state(
                pilot,
                lambda: not app.query_one("#live-similarity-details").display,
                "turning similarity off did not collapse the fields",
            )
            settings.set_locked(True)
            settings.set_locked(False)
            assert not app.query_one("#live-similarity-enabled", Checkbox).value
            assert not app.query_one("#live-similarity-details").display
            assert settings.get_identity() is None
            assert reference.value == "Timeout while waiting"

    asyncio.run(run_check())


@pytest.mark.parametrize("value", ["", "nan", "inf", "-0.1", "100.1"])
def test_invalid_threshold_stays_editable_until_valid(value):
    async def run_check():
        app = _LiveSimilaritySettingsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            settings = await _settle(app, pilot)
            settings.set_reference("Reference")
            threshold = app.query_one("#live-similarity-threshold", Input)
            threshold.value = value
            await wait_for_ui_state(
                pilot,
                lambda: threshold.value == value,
                "invalid threshold edit was discarded",
            )
            with pytest.raises(
                ValueError, match="相似度門檻必須是 0 到 100 的有限數字"
            ):
                settings.get_identity()
            with pytest.raises(
                ValueError, match="相似度門檻必須是 0 到 100 的有限數字"
            ):
                settings.as_request(_base_request())
            assert threshold.value == value

            threshold.value = "100"
            await wait_for_ui_state(
                pilot,
                lambda: threshold.value == "100",
                "valid threshold could not replace the invalid value",
            )
            assert settings.get_identity() == ("Reference", 100.0)
            assert isinstance(settings.as_request(_base_request()), LiveAnalysisRequest)

    asyncio.run(run_check())


@pytest.mark.parametrize(("value", "expected"), [("0", 0.0), ("100", 100.0)])
def test_threshold_accepts_inclusive_limits(value, expected):
    async def run_check():
        app = _LiveSimilaritySettingsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            settings = await _settle(app, pilot)
            settings.set_reference("Reference")
            threshold = app.query_one("#live-similarity-threshold", Input)
            threshold.value = value
            await wait_for_ui_state(
                pilot,
                lambda: threshold.value == value,
                "threshold boundary input was not retained",
            )
            assert settings.get_identity() == ("Reference", expected)
            assert isinstance(settings.as_request(_base_request()), LiveAnalysisRequest)

    asyncio.run(run_check())


def test_empty_reference_is_rejected_only_when_similarity_is_enabled():
    async def run_check():
        app = _LiveSimilaritySettingsApp()
        async with app.run_test(size=(80, 24)) as pilot:
            settings = await _settle(app, pilot)
            settings.set_reference("   ")
            with pytest.raises(ValueError, match="參考訊息不可空白"):
                settings.get_identity()
            assert app.query_one("#live-similarity-reference", Input).value == "   "

            await pilot.click(app.query_one("#live-similarity-enabled", Checkbox))
            await wait_for_ui_state(
                pilot,
                lambda: not app.query_one("#live-similarity-enabled", Checkbox).value,
                "similarity checkbox did not turn off",
            )
            assert settings.get_identity() is None
            assert settings.as_request(_base_request()) == _base_request()

    asyncio.run(run_check())


@pytest.mark.parametrize(
    ("size", "narrow"),
    [((80, 24), False), ((40, 20), True), ((80, 8), False)],
)
def test_controls_fit_wide_narrow_and_short_scrollable_form(size, narrow):
    async def run_check():
        app = _LiveSimilaritySettingsApp(narrow=narrow)
        async with app.run_test(size=size) as pilot:
            settings = await _settle(app, pilot)
            settings.set_reference("A long reference message that should wrap cleanly")
            reference = app.query_one("#live-similarity-reference", Input)
            threshold = app.query_one("#live-similarity-threshold", Input)
            hint = app.query_one("#live-similarity-hint", Static)
            await wait_for_layout_to_settle(
                pilot, (settings, reference, threshold, hint)
            )
            host = app.query_one("#similarity-host", VerticalScroll)
            assert settings.region.width <= host.content_region.width
            assert hint.region.height > 0
            assert all(
                widget.region.x >= host.content_region.x
                and widget.region.x + widget.region.width
                <= host.content_region.x + host.content_region.width
                for widget in (settings, reference, threshold, hint)
            )
            assert "比對正文" in hint.render().plain
            assert "大小寫" in hint.render().plain
            assert "取交集" in hint.render().plain

    asyncio.run(run_check())
