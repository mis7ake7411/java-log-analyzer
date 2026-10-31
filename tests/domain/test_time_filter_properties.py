from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from hypothesis import example, given, strategies as st

from log_analyzer.domain.parser import ParseOptions, parse_log_directory


_BASE_TIME = datetime(2026, 6, 6, 10, 0, 0)


def _timestamp(offset_seconds: int) -> str:
    return (_BASE_TIME + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )[:-3]


@example(
    event_offsets=[-1, 0, 1],
    start_offset=0,
    end_offset=0,
    use_start=True,
    use_end=True,
)
@example(
    event_offsets=[-1, 0, 1],
    start_offset=0,
    end_offset=0,
    use_start=True,
    use_end=False,
)
@example(
    event_offsets=[-1, 0, 1],
    start_offset=0,
    end_offset=0,
    use_start=False,
    use_end=True,
)
@given(
    event_offsets=st.lists(
        st.integers(min_value=-300, max_value=600),
        min_size=1,
        max_size=20,
        unique=True,
    ),
    start_offset=st.integers(min_value=0, max_value=300),
    end_offset=st.integers(min_value=0, max_value=300),
    use_start=st.booleans(),
    use_end=st.booleans(),
)
def test_time_filter_is_inclusive_and_preserves_every_in_range_event(
    event_offsets: list[int],
    start_offset: int,
    end_offset: int,
    use_start: bool,
    use_end: bool,
) -> None:
    """以隨機事件集合確認起訖邊界含入且區間外事件不會混入。"""
    lower_offset, upper_offset = sorted((start_offset, end_offset))
    offsets = sorted(set(event_offsets) | {lower_offset, upper_offset})
    start_time = (
        _BASE_TIME + timedelta(seconds=lower_offset) if use_start else None
    )
    end_time = _BASE_TIME + timedelta(seconds=upper_offset) if use_end else None
    expected_offsets = [
        offset
        for offset in offsets
        if (start_time is None or offset >= lower_offset)
        and (end_time is None or offset <= upper_offset)
    ]

    with tempfile.TemporaryDirectory() as temp_dir:
        log_file = Path(temp_dir) / "events.log"
        log_file.write_text(
            "".join(
                f"{_timestamp(offset)} [worker] INFO  com.example.Service - "
                f"event-{offset}\n"
                for offset in offsets
            ),
            encoding="utf-8",
        )

        counts, logs = parse_log_directory(
            temp_dir,
            ParseOptions(start_time=start_time, end_time=end_time),
        )
        try:
            actual = [(entry["timestamp"], entry["message"]) for entry in logs]
            expected = [
                (_timestamp(offset), f"event-{offset}")
                for offset in expected_offsets
            ]

            assert counts["INFO"] == len(expected_offsets)
            assert actual == expected
        finally:
            logs.release()
