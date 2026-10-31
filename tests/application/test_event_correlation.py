from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from log_analyzer.application.events.correlation import find_correlated_occurrences


def _occurrence(
    filename: str,
    line_number: int,
    timestamp: str,
    mdc: dict[str, str] | None = None,
) -> dict[str, object]:
    occurrence: dict[str, object] = {
        "filename": filename,
        "line_number": line_number,
        "timestamp": timestamp,
        "thread": "worker-1",
    }
    if mdc is not None:
        occurrence["mdc"] = mdc
    return occurrence


def _group(
    index: int,
    occurrences: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "level": "ERROR",
        "logger": f"example.Service{index}",
        "message": f"failure {index}",
        "occurrences": occurrences,
    }


def test_matches_exact_mdc_key_and_value_case_sensitively() -> None:
    groups = [
        _group(
            0,
            [
                _occurrence("app.log", 1, "2026-10-20T12:00:00", {"traceId": "abc"}),
                _occurrence("app.log", 2, "2026-10-20T12:00:01", {"traceId": "abcd"}),
                _occurrence("app.log", 3, "2026-10-20T12:00:02", {"TraceId": "abc"}),
                _occurrence("app.log", 4, "2026-10-20T12:00:03", {"requestId": "abc"}),
                _occurrence("app.log", 5, "2026-10-20T12:00:04", {"traceId": "ABC"}),
            ],
        )
    ]

    actual = asyncio.run(find_correlated_occurrences(groups, "traceId", "abc"))

    assert [(item.group_index, item.occurrence_index) for item in actual] == [(0, 0)]


def test_same_group_occurrences_keep_their_own_mdc_values() -> None:
    groups = [
        _group(
            0,
            [
                _occurrence("a.log", 10, "2026-10-20T12:00:00", {"traceId": "first"}),
                _occurrence("b.log", 20, "2026-10-20T12:00:01", {"traceId": "second"}),
            ],
        )
    ]
    groups[0]["message"] = "Failure header"
    groups[0]["message_body"] = "continued detail"

    actual = asyncio.run(find_correlated_occurrences(groups, "traceId", "second"))

    assert [(item.group_index, item.occurrence_index) for item in actual] == [(0, 1)]
    assert actual[0].message == "Failure header\ncontinued detail"
    assert actual[0].logger == "example.Service0"


def test_results_sort_by_time_file_line_then_source_indices() -> None:
    timestamp = "2026-10-20T12:00:00"
    groups = [
        _group(
            0,
            [
                _occurrence("z.log", 20, timestamp, {"traceId": "same"}),
                _occurrence("a.log", 8, timestamp, {"traceId": "same"}),
                _occurrence("a.log", 2, timestamp, {"traceId": "same"}),
            ],
        ),
        _group(1, [_occurrence("a.log", 2, timestamp, {"traceId": "same"})]),
    ]

    actual = asyncio.run(find_correlated_occurrences(groups, "traceId", "same"))

    assert [(item.group_index, item.occurrence_index) for item in actual] == [
        (0, 2),
        (1, 0),
        (0, 1),
        (0, 0),
    ]


def test_millisecond_separator_does_not_change_chronological_sort() -> None:
    groups = [
        _group(0, [_occurrence("app.log", 4, "2026-10-20 12:00:00,900", {"traceId": "id"})]),
        _group(1, [_occurrence("app.log", 1, "2026-10-20 12:00:00.100", {"traceId": "id"})]),
        _group(2, [_occurrence("app.log", 2, "2026-10-20 12:00:00,100", {"traceId": "id"})]),
        _group(3, [_occurrence("app.log", 3, "2026-10-20 12:00:00.900", {"traceId": "id"})]),
    ]

    actual = asyncio.run(find_correlated_occurrences(groups, "traceId", "id"))

    assert [(item.group_index, item.occurrence_index) for item in actual] == [
        (1, 0),
        (2, 0),
        (3, 0),
        (0, 0),
    ]


def test_missing_mdc_in_legacy_groups_returns_no_matches() -> None:
    groups = [_group(0, [_occurrence("old.log", 1, "2026-10-20T12:00:00")])]

    assert asyncio.run(find_correlated_occurrences(groups, "traceId", "abc")) == []


@pytest.mark.parametrize(("key", "value"), [("", "abc"), ("traceId", "")])
def test_empty_key_or_value_is_rejected(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        asyncio.run(find_correlated_occurrences([], key, value))


def test_scan_can_be_cancelled_between_group_batches() -> None:
    groups = [_group(index, []) for index in range(64)]

    async def run() -> None:
        task = asyncio.create_task(
            find_correlated_occurrences(groups, "traceId", "unused")
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_query_does_not_mutate_group_or_occurrence_data() -> None:
    groups = [
        _group(
            0,
            [_occurrence("app.log", 1, "2026-10-20T12:00:00", {"traceId": "abc"})],
        )
    ]
    before = deepcopy(groups)

    asyncio.run(find_correlated_occurrences(groups, "traceId", "abc"))

    assert groups == before
