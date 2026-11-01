from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import string

from hypothesis import example, given, strategies as st

from log_analyzer.application.events.correlation import (
    CorrelatedOccurrence,
    find_correlated_occurrences,
)


_KEY = "traceId"
_TARGET_KEYS = ("traceId", "requestId", "租戶", "customKey")
_MATCH_MODES = ("match", "value-suffix", "value-case", "key-case", "key-suffix")


@st.composite
def _timestamps(draw: st.DrawFn) -> str:
    second = draw(st.integers(min_value=0, max_value=2))
    millisecond = draw(st.integers(min_value=0, max_value=999))
    separator = draw(st.sampled_from((",", ".")))
    return f"2026-10-20 12:00:{second:02d}{separator}{millisecond:03d}"


_record = st.tuples(
    _timestamps(),
    st.sampled_from(("a.log", "b.log", "z.log")),
    st.integers(min_value=1, max_value=4),
    st.sampled_from(_MATCH_MODES),
)


@st.composite
def _occurrence_batches(draw: st.DrawFn) -> list[list[tuple[str, str, int, str]]]:
    return draw(
        st.lists(
            st.lists(_record, min_size=1, max_size=4),
            min_size=1,
            max_size=6,
        )
    )


def _group(index: int, occurrences: list[dict[str, object]]) -> dict[str, object]:
    return {
        "level": "ERROR",
        "logger": f"example.Service{index}",
        "message": f"failure {index}",
        "occurrences": occurrences,
    }


def _occurrence(
    timestamp: str,
    filename: str,
    line_number: int,
    mdc: dict[str, str],
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "filename": filename,
        "line_number": line_number,
        "thread": "worker-1",
        "mdc": mdc,
    }


@example(
    target_key="traceId",
    target_value="abc",
    batches=[
        [
            ("2026-10-20 12:00:00,100", "z.log", 20, "match"),
            ("2026-10-20 12:00:00.100", "a.log", 8, "match"),
            ("2026-10-20 12:00:00,100", "a.log", 2, "match"),
        ],
        [("2026-10-20 12:00:00.100", "a.log", 2, "match")],
    ],
)
@example(
    target_key="requestId",
    target_value="trace-7",
    batches=[[("2026-10-20 12:00:00.001", "app.log", 1, "value-case")]],
)
@example(
    target_key="租戶",
    target_value="tenant7",
    batches=[[("2026-10-20 12:00:00.001", "app.log", 1, "key-suffix")]],
)
@given(
    target_key=st.sampled_from(_TARGET_KEYS),
    target_value=st.text(
        alphabet=string.ascii_lowercase,
        min_size=1,
        max_size=12,
    ),
    batches=_occurrence_batches(),
)
def test_correlation_matches_exact_mdc_pairs_and_keeps_source_tie_order(
    target_key: str,
    target_value: str,
    batches: list[list[tuple[str, str, int, str]]],
) -> None:
    """以精確值與近似值誘餌確認篩選和同排序鍵來源順序。"""
    groups: list[dict[str, object]] = []
    expected_rows: list[tuple[int, int, str, str, int]] = []

    for group_index, records in enumerate(batches):
        occurrences = []
        for occurrence_index, (timestamp, filename, line_number, match_mode) in enumerate(
            records
        ):
            wrong_case_key = target_key.swapcase()
            if wrong_case_key == target_key:
                wrong_case_key = f"{target_key}-case"
            mdc = {
                wrong_case_key: target_value,
                f"{target_key}-suffix": target_value,
            }
            if match_mode == "match":
                mdc[target_key] = target_value
            elif match_mode == "value-suffix":
                mdc[target_key] = f"{target_value}-decoy"
            elif match_mode == "value-case":
                mdc[target_key] = target_value.swapcase()
            else:
                mdc[target_key] = f"{target_value}-decoy"
            occurrences.append(
                _occurrence(timestamp, filename, line_number, mdc)
            )
            if match_mode == "match":
                expected_rows.append(
                    (group_index, occurrence_index, timestamp, filename, line_number)
                )
        groups.append(_group(group_index, occurrences))

    before = deepcopy(groups)
    expected_rows.sort(
        key=lambda row: (
            datetime.fromisoformat(row[2].replace(",", ".")),
            row[3],
            row[4],
            row[0],
            row[1],
        )
    )

    actual = asyncio.run(find_correlated_occurrences(groups, target_key, target_value))

    assert [
        (item.group_index, item.occurrence_index, item.timestamp, item.filename, item.line_number)
        for item in actual
    ] == expected_rows
    assert groups == before


@example(
    timestamps=[
        "2026-10-20 12:00:00,900",
        "2026-10-20 12:00:00.100",
        "2026-10-20 12:00:00,100",
        "2026-10-20 12:00:00.900",
    ]
)
@given(timestamps=st.lists(_timestamps(), min_size=1, max_size=20))
def test_correlation_order_is_stable_when_primary_sort_keys_are_unique(
    timestamps: list[str],
) -> None:
    """固定時間與不同來源檔名時，輸入群組順序不影響結果排序。"""
    groups = [
        _group(
            index,
            [
                _occurrence(
                    timestamp,
                    f"{index:04}.log",
                    index + 1,
                    {_KEY: "shared-value"},
                )
            ],
        )
        for index, timestamp in enumerate(timestamps)
    ]

    forward = asyncio.run(find_correlated_occurrences(groups, _KEY, "shared-value"))
    reverse_groups = list(reversed(groups))
    reverse = asyncio.run(
        find_correlated_occurrences(reverse_groups, _KEY, "shared-value")
    )

    expected_indices = sorted(
        range(len(timestamps)),
        key=lambda index: (
            datetime.fromisoformat(timestamps[index].replace(",", ".")),
            f"{index:04}.log",
            index + 1,
        ),
    )
    expected_forward = [
        (index, f"{index:04}.log", index + 1) for index in expected_indices
    ]
    expected_reverse = [
        (len(timestamps) - index - 1, f"{index:04}.log", index + 1)
        for index in expected_indices
    ]

    def source_projection(
        items: list[CorrelatedOccurrence],
    ) -> list[tuple[int, str, int]]:
        return [
            (item.group_index, item.filename, item.line_number)
            for item in items
        ]

    assert len(forward) == len(groups)
    assert len(reverse) == len(reverse_groups)
    assert source_projection(forward) == expected_forward
    assert source_projection(reverse) == expected_reverse
