import pytest

from log_analyzer.application.dashboard_summary import aggregate_dashboard_summary
from log_analyzer.domain.analysis_control import AnalysisCancelled, AnalysisControl


def _group(occurrences):
    return {
        "timestamp": "2026-10-24 10:00:00.000",
        "thread": "worker-a",
        "logger": "Service",
        "message": "RuntimeException: failed",
        "count": len(occurrences),
        "occurrences": [
            {"timestamp": timestamp, "thread": thread, "filename": "app.log", "line_number": i}
            for i, (timestamp, thread) in enumerate(occurrences, 1)
        ],
    }


def test_time_hotspots_count_each_occurrence_and_each_group_once_per_hour():
    first = _group([
        ("2026-10-24 10:00:00.000", "worker-a"),
        ("2026-10-24 10:59:59.999", "worker-a"),
        ("2026-10-24 11:00:00.000", "worker-a"),
        ("2026-10-25 10:00:00.000", "worker-a"),
    ])
    second = _group([("2026-10-24 11:30:00.000", "worker-b")])
    summary, total = aggregate_dashboard_summary(iter([first, second]))
    assert summary.time_hotspots == (
        ("2026-10-24 11:00", 2, 2),
        ("2026-10-24 10:00", 1, 2),
        ("2026-10-25 10:00", 1, 1),
    )
    assert total == 5
    assert summary.logger_summary == (("Service", 2, 5),)
    assert summary.exception_summary == (("RuntimeException: failed", 2, 5),)


def test_thread_distribution_counts_each_occurrence_without_duplicate_groups():
    first = _group([
        ("2026-10-24 10:00:00", "worker-a"),
        ("2026-10-24 10:00:01", "worker-b"),
        ("2026-10-24 10:00:02", "worker-b"),
    ])
    second = _group([("2026-10-24 10:00:03", "worker-a")])
    summary, total = aggregate_dashboard_summary([first, second])
    assert summary.thread_summary == (("worker-a", 2, 2), ("worker-b", 1, 2))
    assert total == 4


@pytest.mark.parametrize("occurrences", [None, []])
def test_legacy_groups_without_occurrences_keep_weighted_summary(occurrences):
    group = _group([])
    group["count"] = 3
    if occurrences is None:
        del group["occurrences"]
    summary, total = aggregate_dashboard_summary([group])
    assert summary.time_hotspots == (("2026-10-24 10:00", 1, 3),)
    assert summary.thread_summary == (("worker-a", 1, 3),)
    assert total == 3


def test_missing_occurrence_fields_do_not_inherit_representative_values():
    summary, total = aggregate_dashboard_summary([
        _group([("", ""), ("2026-10-24 11:00:00", "worker-b")])
    ])
    assert summary.time_hotspots == (("2026-10-24 11:00", 1, 1),)
    assert summary.thread_summary == (("worker-b", 1, 1),)
    assert total == 2


def test_summary_can_cancel_while_reading_a_single_large_group():
    control = AnalysisControl()

    def occurrences():
        yield {"timestamp": "2026-10-24 10:00:00", "thread": "worker-a"}
        control.cancel()
        yield {"timestamp": "2026-10-24 11:00:00", "thread": "worker-b"}
        pytest.fail("summary continued reading after cancellation")

    group = _group([])
    group["occurrences"] = occurrences()
    group["count"] = 2
    with pytest.raises(AnalysisCancelled):
        aggregate_dashboard_summary([group], control=control)
