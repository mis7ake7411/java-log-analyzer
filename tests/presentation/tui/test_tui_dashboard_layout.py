from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from rich.cells import cell_len
from rich.console import Console

from log_analyzer.presentation.tui.views import build_dashboard_view


def _result(
    *,
    exported_files: tuple[str, ...] = (),
    input_path: str = "C:/logs",
    keyword: str = "timeout",
    level_summary: tuple[tuple[str, int], ...] = (
        ("DEBUG", 602),
        ("ERROR", 5),
        ("INFO", 128),
        ("WARN", 3),
    ),
) -> SimpleNamespace:
    return SimpleNamespace(
        total_logs=738,
        matched_groups=285,
        matched_occurrences=738,
        format_name="csv",
        input_path=input_path,
        output_path="C:/exports/report.csv",
        exported_files=exported_files,
        keyword=keyword,
        selected_levels=("DEBUG", "ERROR", "INFO", "WARN"),
        sort_by="time",
        ignore_case=False,
        level_summary=level_summary,
        matched_logs=(),
        dashboard_summary=SimpleNamespace(
            exception_summary=(),
            time_hotspots=(),
            logger_summary=(("com.test.Api", 12, 602),),
            thread_summary=(("http-worker-1", 12, 602),),
        ),
    )


def _render(
    result: SimpleNamespace,
    width: int,
    compact: bool,
    legacy_windows: bool | None = None,
) -> str:
    console = Console(
        width=width,
        record=True,
        color_system=None,
        legacy_windows=legacy_windows,
    )
    console.print(build_dashboard_view(result, compact=compact))
    return console.export_text()


def _title_line(output: str, title: str) -> str:
    return next(line for line in output.splitlines() if title in line)


def _panel_geometry(line: str, expected_count: int) -> tuple[list[int], list[int], int]:
    left_edges = [
        cell_len(line[:index])
        for index, character in enumerate(line)
        if character in {"┌", "╭"}
    ]
    right_edges = [
        cell_len(line[:index])
        for index, character in enumerate(line)
        if character in {"┐", "╮"}
    ]
    assert len(left_edges) == expected_count
    assert len(right_edges) == expected_count
    widths = [right - left + 1 for left, right in zip(left_edges, right_edges)]
    gaps = [
        left_edges[index + 1] - right_edges[index] - 1
        for index in range(len(right_edges) - 1)
    ]
    return widths, gaps, right_edges[-1]


@pytest.mark.parametrize("legacy_windows", [False, True], ids=("rounded", "legacy"))
def test_metric_cards_use_available_width_even_when_compact(legacy_windows: bool) -> None:
    for width in (80, 100, 140):
        for compact in (False, True):
            output = _render(
                _result(),
                width=width,
                compact=compact,
                legacy_windows=legacy_windows,
            )

            line = _title_line(output, "總 Log")
            assert "群組" in line
            assert "命中" in line
            assert ("┌" if legacy_windows else "╭") in line
            assert ("┐" if legacy_windows else "╮") in line
            widths, gaps, right_edge = _panel_geometry(line, expected_count=3)
            assert max(widths) - min(widths) <= 1
            assert max(gaps) <= 2
            assert right_edge == width - 1


@pytest.mark.parametrize("legacy_windows", [False, True], ids=("rounded", "legacy"))
def test_logger_and_thread_panels_fill_equally_and_adapt_to_actual_width(
    legacy_windows: bool,
) -> None:
    for width in (45, 80, 100, 140):
        for compact in (False, True):
            output = _render(
                _result(),
                width=width,
                compact=compact,
                legacy_windows=legacy_windows,
            )
            logger_line = next(
                line
                for line in output.splitlines()
                if "Logger" in line
                and ("┌" in line or "╭" in line)
                and "Logger / Thread 分布" not in line
            )
            thread_line = next(
                line
                for line in output.splitlines()
                if "Thread" in line
                and ("┌" in line or "╭" in line)
                and "Logger / Thread 分布" not in line
            )

            for panel_line in (logger_line, thread_line):
                assert ("┌" if legacy_windows else "╭") in panel_line
                assert ("┐" if legacy_windows else "╮") in panel_line

            if width >= 100:
                assert logger_line == thread_line
                widths, gaps, right_edge = _panel_geometry(logger_line, expected_count=2)
                assert max(widths) - min(widths) <= 1
                assert max(gaps) <= 2
                assert width - 1 - right_edge <= 4
            else:
                assert logger_line != thread_line


def test_metric_cards_stack_at_narrow_width_for_both_modes() -> None:
    for compact in (False, True):
        output = _render(_result(), width=45, compact=compact)

        title_lines = [
            _title_line(output, title)
            for title in ("總 Log", "群組", "命中")
        ]
        assert len(set(title_lines)) == 3


def test_dashboard_status_is_concise_and_contains_export_state() -> None:
    pending = _render(_result(), width=100, compact=True)
    exported = _render(
        _result(exported_files=("C:/exports/report.csv",)),
        width=100,
        compact=True,
    )

    assert "執行狀態" in pending
    assert "分析完成" in pending
    assert pending.index("未匯出") < pending.index("總 Log")
    assert "先看摘要" not in pending
    assert exported.index("已匯出 CSV") < exported.index("總 Log")


def test_analysis_information_and_level_distribution_are_full_width_sections() -> None:
    for compact in (False, True):
        output = _render(_result(), width=140, compact=compact)

        analysis_line = _title_line(output, "分析資訊")
        level_line = _title_line(output, "Level 分布")
        assert "Level 分布" not in analysis_line
        assert "分析資訊" not in level_line
        section_titles = (
            "執行狀態",
            "總 Log",
            "分析資訊",
            "Level 分布",
            "例外群組摘要",
            "時間熱點",
            "Logger / Thread 分布",
        )
        positions = [output.index(title) for title in section_titles]
        assert positions == sorted(positions)


def test_level_bars_expand_with_width_and_percent_is_share_of_total() -> None:
    wide = _render(_result(), width=140, compact=True)
    narrow = _render(_result(), width=45, compact=True)
    wide_debug_row = next(
        line for line in wide.splitlines() if "DEBUG" in line and "602" in line
    )
    narrow_debug_row = next(
        line for line in narrow.splitlines() if "DEBUG" in line and "602" in line
    )

    assert "81.6%" in wide_debug_row
    assert "占比以本次結果的總筆數計算" in wide
    assert "相對最高" in wide
    assert wide_debug_row.count("█") > narrow_debug_row.count("█")
    assert wide_debug_row.count("█") > 18


def test_level_bars_keep_zero_and_empty_summaries_safe() -> None:
    all_zero = _render(
        _result(level_summary=(("DEBUG", 0), ("ERROR", 0))),
        width=80,
        compact=False,
    )
    empty = _render(_result(level_summary=()), width=45, compact=True)

    assert "0.0%" in all_zero
    assert not re.search(r"█+", all_zero)
    assert "N/A" in empty
    assert "沒有可顯示的統計" in empty


def test_long_metadata_is_literal_and_survives_narrow_wrapping() -> None:
    literal_path = "C:/logs/[bold red]application[/bold red]/service.log"
    literal_keyword = "[link=https://example.test]timeout[/link]"
    output = _render(
        _result(input_path=literal_path, keyword=literal_keyword),
        width=45,
        compact=True,
    )

    metadata_output = output.split("分析資訊", 1)[1].split("Level 分布", 1)[0]
    whitespace_free_output = re.sub(r"[\s│]+", "", metadata_output)
    assert re.sub(r"\s+", "", literal_path) in whitespace_free_output
    assert re.sub(r"\s+", "", literal_keyword) in whitespace_free_output
    assert output.rfind("service.log") != -1
    assert output.rfind("[/link]") != -1
