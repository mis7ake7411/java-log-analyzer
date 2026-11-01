from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from textual.app import App

from log_analyzer.domain.log_types import FileScanCoverage, ScanDiagnostics
from log_analyzer.presentation.tui.app import LogAnalyzerApp
from log_analyzer.presentation.tui.components.dashboard_card import dashboard_card
from log_analyzer.presentation.tui.dashboard_styles import (
    DASHBOARD_CATEGORY_APPEARANCES,
    DASHBOARD_PALETTE,
    DashboardCardCategory,
)
from log_analyzer.presentation.tui.components.dashboard import (
    DashboardView,
    _ProgressBar,
    _build_exception_summary_panel,
    _build_logger_thread_distribution_panel,
    _build_top_events_panel,
    _build_time_hotspot_panel,
    _dashboard_link_style,
    _distribution_table,
    _metric_card,
)
from log_analyzer.presentation.tui.views import (
    _build_status_panel,
    build_idle_view,
    build_dashboard_view,
    build_error_view,
    build_loading_view,
    build_zero_result_view,
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


def test_dashboard_cards_share_category_appearance_and_neutral_background() -> None:
    cards = [
        _metric_card("總 Log", "12", "整體筆數"),
        _metric_card("群組", "3", "符合條件的事件群組"),
        _metric_card("命中", "5", "條件觸發總次數"),
    ]
    summary_statuses = [
        build_idle_view(),
        build_loading_view(),
        build_error_view("literal [red] error", "literal <message>"),
        build_zero_result_view(ScanDiagnostics()),
        _build_status_panel("summary", (Text("body"),)),
    ]
    events = [
        _build_exception_summary_panel(()),
        _build_exception_summary_panel((("ValueError", 1, 2),)),
        _build_time_hotspot_panel(()),
        _build_top_events_panel(
            (SimpleNamespace(group_index=0, level="ERROR", message="disk", count=2),),
            (SimpleNamespace(group_index=0),),
            5,
        ),
    ]
    distribution = _build_logger_thread_distribution_panel(
        (("com.example.Logger", 2, 4),), (("worker-1", 1, 4),)
    )
    nested_distribution = distribution.renderable.panels
    context_dashboard = _build_style_test_dashboard()
    context_panels = context_dashboard.renderables[2].renderables
    coverage_panels = [
        panel
        for panel in context_dashboard.renderables
        if isinstance(panel, Panel) and panel.title.plain == "逐檔解析診斷"
    ]
    assert len(coverage_panels) == 1
    cards_by_category = {
        DashboardCardCategory.SUMMARY: [*cards, *summary_statuses],
        DashboardCardCategory.CONTEXT: [context_panels[0], *coverage_panels],
        DashboardCardCategory.DISTRIBUTION: [
            context_panels[1],
            distribution,
            *nested_distribution,
            _distribution_table("Logger", (("com.example.Logger", 2, 4),)),
            _distribution_table("Thread", (("worker-1", 1, 4),)),
        ],
        DashboardCardCategory.EVENTS: events,
    }

    category_border_colors = set()
    category_title_colors = set()
    for category, panels in cards_by_category.items():
        appearance = DASHBOARD_CATEGORY_APPEARANCES[category]
        category_border_colors.add(appearance.border)
        category_title_colors.add(appearance.title)
        for panel in panels:
            assert panel.border_style == appearance.border_style
            assert panel.title.style == appearance.title_style
            assert panel.style == DASHBOARD_PALETTE.panel_style

    assert len(category_border_colors) == len(cards_by_category)
    assert len(category_title_colors) == len(cards_by_category)
    assert {panel.border_style for panel in cards} == {
        DASHBOARD_CATEGORY_APPEARANCES[DashboardCardCategory.SUMMARY].border_style
    }
    assert all(
        value.style == DASHBOARD_PALETTE.value_bold_style
        and caption.style == DASHBOARD_PALETTE.secondary_style
        for card in cards
        for value, caption in [card.renderable.renderables]
    )
    assert summary_statuses[2].renderable.renderables[0].style == (
        DASHBOARD_PALETTE.error_style
    )
    assert summary_statuses[3].renderable.renderables[0].style == (
        DASHBOARD_PALETTE.warning_style
    )

    empty_exception = _build_exception_summary_panel(())
    empty_lines = empty_exception.renderable.renderables
    assert all(
        isinstance(line, Text)
        and line.style.color.triplet == _truecolor_triplet(DASHBOARD_PALETTE.secondary)
        for line in empty_lines
    )


def _build_style_test_dashboard():
    event = SimpleNamespace(group_index=0, level="ERROR", message="disk", count=2)
    return build_dashboard_view(
        SimpleNamespace(
            total_logs=2,
            matched_groups=1,
            matched_occurrences=2,
            format_name="csv",
            input_path="C:/logs",
            analysis_request=SimpleNamespace(path="C:/logs"),
            exported_files=(),
            keyword="",
            selected_levels=(),
            sort_by="time",
            ignore_case=False,
            level_summary=(("ERROR", 2),),
            matched_logs=(),
            diagnostics=ScanDiagnostics(
                2,
                4,
                4,
                4,
                4,
                1,
                3,
                file_coverage=[
                    FileScanCoverage(
                        filename="app.log",
                        total_lines=2,
                        parsed_entries=1,
                        unrecognized_lines=1,
                    )
                ],
            ),
            dashboard_summary=SimpleNamespace(
                exception_summary=(),
                time_hotspots=(),
                logger_summary=(("com.example.Logger", 2, 2),),
                thread_summary=(("worker-1", 1, 2),),
                top_events=(event,),
            ),
        ),
        compact=False,
        navigation_generation=5,
    )


def test_category_colors_render_on_title_and_border_without_coloring_body() -> None:
    category = DashboardCardCategory.EVENTS
    appearance = DASHBOARD_CATEGORY_APPEARANCES[category]
    action = "app.open_dashboard_target(5, 0)"
    body = Text("neutral body", style=DASHBOARD_PALETTE.secondary_style)
    body.append("\n")
    body.append("clickable entry", style=_dashboard_link_style(action))

    panel = dashboard_card(body, "Events", category=category)
    segments = list(Console(width=60, color_system="truecolor").render(panel))
    title_segment = next(segment for segment in segments if "Events" in segment.text)
    border_segment = next(
        segment
        for segment in segments
        if segment.style.color
        and segment.style.color.triplet == _truecolor_triplet(appearance.border)
    )
    body_segment = next(
        segment for segment in segments if "neutral body" in segment.text
    )
    link_segment = next(
        segment for segment in segments if "clickable entry" in segment.text
    )

    assert title_segment.style.color.triplet == _truecolor_triplet(appearance.title)
    assert title_segment.style.color.triplet != _truecolor_triplet(
        DASHBOARD_PALETTE.title
    )
    assert border_segment.style.color.triplet == _truecolor_triplet(appearance.border)
    assert border_segment.style.color.triplet != _truecolor_triplet(
        DASHBOARD_PALETTE.border
    )
    assert body_segment.style.color.triplet == _truecolor_triplet(
        DASHBOARD_PALETTE.secondary
    )
    assert link_segment.style.meta == {"@click": action}


def test_category_styles_copy_rich_title_text_without_mutating_it() -> None:
    title = Text("literal [red] title")
    title.stylize("underline", 0, 7)
    original_plain = title.plain
    original_spans = list(title.spans)

    panel = dashboard_card(
        Text("body", style=DASHBOARD_PALETTE.secondary_style),
        title,
        category=DashboardCardCategory.CONTEXT,
    )

    assert panel.title is not title
    assert panel.title.plain == original_plain
    assert all(span in panel.title.spans for span in original_spans)
    assert title.plain == original_plain
    assert title.spans == original_spans


def test_dashboard_builder_returns_standalone_rich_component() -> None:
    dashboard = _build_style_test_dashboard()

    assert isinstance(dashboard, DashboardView)
    assert isinstance(dashboard, Group)
    assert not hasattr(dashboard, "result")
    assert not hasattr(dashboard, "analysis")

    output = StringIO()
    Console(file=output, width=80, color_system=None).print(dashboard)
    assert "分析資訊" in output.getvalue()
    assert "Level 分布" in output.getvalue()


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
    context = DASHBOARD_CATEGORY_APPEARANCES[DashboardCardCategory.CONTEXT]

    assert isinstance(warning_card, Panel)
    assert warning_card.title.plain == "本次掃描編碼提醒"
    assert warning_card.border_style == context.border_style
    assert warning_card.title.style == context.title_style
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
