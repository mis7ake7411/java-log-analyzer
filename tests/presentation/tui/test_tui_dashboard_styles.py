from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

from rich.cells import cell_len
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from textual.app import App

from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.dashboard_styles import (
    DASHBOARD_PALETTE,
    dashboard_card,
)
from log_analyzer.presentation.tui.views import (
    _ProgressBar,
    _build_exception_summary_panel,
    _build_logger_thread_distribution_panel,
    _build_time_hotspot_panel,
    _dashboard_link_style,
    _distribution_table,
    _metric_card,
    build_idle_view,
)
from log_analyzer.presentation.tui.controllers.results import ResultStateMixin


def _truecolor_triplet(hex_color: str) -> tuple[int, int, int]:
    color = hex_color.removeprefix("#")
    return tuple(int(color[index : index + 2], 16) for index in (0, 2, 4))


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [value / 255 for value in _truecolor_triplet(color)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_dashboard_card_uses_explicit_truecolor_styles_and_literal_text() -> None:
    unsafe_title = "<title> [bold]literal[/bold]"
    unsafe_body = "<message> [red]literal[/red] & text"
    body = Text(unsafe_body, style=DASHBOARD_PALETTE.secondary_style)

    card = dashboard_card(body, unsafe_title)

    assert isinstance(card, Panel)
    assert card.title.plain == unsafe_title
    assert card.title.style.color.triplet == _truecolor_triplet(DASHBOARD_PALETTE.title)
    assert card.border_style.color.triplet == _truecolor_triplet(
        DASHBOARD_PALETTE.border
    )
    assert card.style.bgcolor.triplet == _truecolor_triplet(
        DASHBOARD_PALETTE.background
    )
    assert body.plain == unsafe_body
    assert body.style.color.triplet == _truecolor_triplet(DASHBOARD_PALETTE.secondary)

    output = StringIO()
    Console(
        file=output,
        force_terminal=True,
        color_system="truecolor",
        width=60,
    ).print(card)
    rendered = output.getvalue()
    assert unsafe_title in rendered
    assert unsafe_body in rendered
    segments = list(Console(width=60, color_system="truecolor").render(card))
    title_segment = next(
        segment for segment in segments if unsafe_title in segment.text
    )
    body_segment = next(segment for segment in segments if unsafe_body in segment.text)
    assert title_segment.style.color.triplet == _truecolor_triplet(
        DASHBOARD_PALETTE.title
    )
    assert body_segment.style.color.triplet == _truecolor_triplet(
        DASHBOARD_PALETTE.secondary
    )
    assert (
        _contrast_ratio(DASHBOARD_PALETTE.secondary, DASHBOARD_PALETTE.background) >= 7
    )


def test_metric_cards_and_summary_panels_share_neutral_styles() -> None:
    cards = [
        _metric_card("總 Log", "12", "整體筆數"),
        _metric_card("群組", "3", "符合條件的事件群組"),
        _metric_card("命中", "5", "條件觸發總次數"),
    ]
    panels = [
        *cards,
        _build_exception_summary_panel(()),
        _build_time_hotspot_panel(()),
        _build_logger_thread_distribution_panel((), ()),
        _distribution_table("Logger", (("com.example.Logger", 2, 4),)),
    ]

    assert all(panel.border_style == DASHBOARD_PALETTE.border_style for panel in panels)
    assert all(panel.style == DASHBOARD_PALETTE.panel_style for panel in panels)
    assert all(card.title.style == DASHBOARD_PALETTE.title_style for card in cards)

    empty_exception = _build_exception_summary_panel(())
    empty_lines = empty_exception.renderable.renderables
    assert all(
        isinstance(line, Text)
        and line.style.color.triplet == _truecolor_triplet(DASHBOARD_PALETTE.secondary)
        for line in empty_lines
    )


def test_status_and_summary_panels_fill_narrow_and_wide_console_widths() -> None:
    for width in (45, 140):
        for panel in (
            build_idle_view(),
            _build_exception_summary_panel(()),
            _build_time_hotspot_panel(()),
        ):
            output = StringIO()
            Console(file=output, width=width, color_system=None).print(panel)
            top_border = output.getvalue().splitlines()[0]

            assert cell_len(top_border) == width


class _RestoreWarningResultState(ResultStateMixin):
    def __init__(self) -> None:
        self._restored_encoding_warning = "Restored warning [yellow] remains literal"

    def _interactive_dashboard(self, result, compact: bool) -> Text:
        return Text("Dashboard")


def test_restored_encoding_warning_uses_shared_neutral_card_and_warning_text() -> None:
    state = _RestoreWarningResultState()

    dashboard = state._build_dashboard_with_restore_warning(None, compact=False)
    warning_card = dashboard.renderables[0]

    assert isinstance(warning_card, Panel)
    assert warning_card.title.plain == "本次掃描編碼提醒"
    assert warning_card.border_style == DASHBOARD_PALETTE.border_style
    assert warning_card.style == DASHBOARD_PALETTE.panel_style
    assert warning_card.padding == (0, 1)
    assert warning_card.expand
    assert warning_card.renderable.plain == state._restored_encoding_warning
    assert warning_card.renderable.style == DASHBOARD_PALETTE.warning_style


def test_dashboard_links_keep_action_metadata_and_static_bars_are_neutral() -> None:
    action = "app.open_dashboard_target(7, 2)"
    link_style = _dashboard_link_style(action)

    assert link_style.color.triplet == _truecolor_triplet(DASHBOARD_PALETTE.link)
    assert link_style.meta == {"@click": action}
    linked_text = Text("<log> [red]literal[/red]", style=link_style)
    assert linked_text.plain == "<log> [red]literal[/red]"
    assert linked_text.style.meta == {"@click": action}

    console = Console(width=12, color_system="truecolor")
    static_segments = list(console.render(_ProgressBar(1, 2)))
    bar_segment = next(segment for segment in static_segments if "█" in segment.text)
    assert bar_segment.style.color.triplet == _truecolor_triplet(
        DASHBOARD_PALETTE.value
    )
    assert not (bar_segment.style.meta or {}).get("@click")

    linked_segments = list(console.render(_ProgressBar(1, 2, action=action)))
    linked_bar = next(segment for segment in linked_segments if "█" in segment.text)
    assert linked_bar.style.color.triplet == _truecolor_triplet(DASHBOARD_PALETTE.link)
    assert linked_bar.style.meta == {"@click": action}


def test_textual_result_box_variables_merge_with_theme_and_match_tcss() -> None:
    app = LogAnalyzerApp()
    base_variables = App.get_css_variables(app)
    css_variables = app.get_css_variables()

    assert all(css_variables[name] == value for name, value in base_variables.items())
    assert {
        name: css_variables[name] for name in DASHBOARD_PALETTE.css_variables
    } == DASHBOARD_PALETTE.css_variables

    root = Path(__file__).resolve().parents[3]
    tcss = (root / "src/log_analyzer/tcss/tui.layout.tcss").read_text(encoding="utf-8")
    result_box = tcss.split("#result-box {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    referenced = set(re.findall(r"\$(dashboard-[a-z-]+)", tcss))

    assert referenced == set(DASHBOARD_PALETTE.css_variables)
    assert all(f"${name}" in result_box for name in DASHBOARD_PALETTE.css_variables)
