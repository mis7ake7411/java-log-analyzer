from collections import Counter
from dataclasses import replace
from datetime import datetime
from math import inf, nan

import pytest

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live import session as live_session_module
from log_analyzer.application.live.conditions import LiveAnalysisRequest
from log_analyzer.application.live.session import LiveSession
from log_analyzer.application.events.similarity import (
    score_message as real_score_message,
)


def _log(
    message: str, level: str = "ERROR", timestamp: str = "2026-10-31 12:00:00.000"
) -> bytes:
    return f"{timestamp} [main] {level} com.test - {message}\n".encode()


def test_live_request_is_a_frozen_analysis_request_with_fixed_similarity_fields():
    base = AnalysisRequest("app.log", levels=None)
    request = LiveAnalysisRequest(
        "app.log", levels=None, similar_to="Database failed", similarity_threshold=85
    )

    assert isinstance(request, AnalysisRequest)
    assert request.similar_to == "Database failed"
    assert request.similarity_threshold == 85
    assert request != base
    assert request != LiveAnalysisRequest(
        "app.log", levels=None, similar_to="Database failed", similarity_threshold=84
    )
    assert request != LiveAnalysisRequest(
        "app.log", levels=None, similar_to="Other failure", similarity_threshold=85
    )


@pytest.mark.parametrize("reference", ["", " \t\r\n "])
def test_live_request_rejects_empty_similarity_reference(reference):
    with pytest.raises(ValueError, match="similar_to"):
        LiveAnalysisRequest("app.log", similar_to=reference)


@pytest.mark.parametrize("threshold", [True, nan, inf, -inf, -0.01, 100.01])
def test_live_request_rejects_invalid_similarity_threshold(threshold):
    with pytest.raises(ValueError, match="similarity_threshold"):
        LiveAnalysisRequest(
            "app.log", similar_to="Database failed", similarity_threshold=threshold
        )


def test_similarity_is_anded_after_existing_level_time_and_keyword_filters(
    tmp_path, monkeypatch
):
    source = tmp_path / "app.log"
    boundary = "2026-10-31 12:00:00.000"
    source.write_bytes(
        _log("Database connection failed", timestamp=boundary)
        + _log("Connection refused", timestamp=boundary)
        + _log("Database connection failed", "INFO", timestamp=boundary)
        + _log("Database connection failed", timestamp="2026-10-31 12:00:01.000")
        + _log("Database connection failed!", timestamp=boundary)
        + _log("Database connection failed", timestamp=boundary)
    )
    request = LiveAnalysisRequest(
        str(source),
        start_dt=datetime(2026, 10, 31, 12),
        end_dt=datetime(2026, 10, 31, 12),
        keyword="database",
        ignore_case=True,
        levels=("ERROR",),
        similar_to="Database connection failed",
        similarity_threshold=100,
    )
    scored = []
    original_score = real_score_message

    def record_score(reference, message):
        scored.append((reference, message))
        return original_score(reference, message)

    monkeypatch.setattr(
        live_session_module, "score_message", record_score, raising=False
    )
    session = LiveSession(request, directory=tmp_path)
    try:
        session.resume()
        events = session.step()

        assert [event["message"] for event in events] == ["Database connection failed"]
        assert session.completed == 5
        assert session.matched == 1
        assert session.level_counts == Counter({"ERROR": 1})
        assert scored == [
            ("Database connection failed", "Database connection failed"),
            ("Database connection failed", "Database connection failed!"),
        ]
        assert session.has_pending
    finally:
        session.close()


def test_pending_similarity_is_scored_once_after_resume_and_once_at_end(
    tmp_path, monkeypatch
):
    source = tmp_path / "app.log"
    source.write_bytes(_log("Database failed") * 2)
    request = LiveAnalysisRequest(
        str(source),
        levels=None,
        similar_to="Database failed",
        similarity_threshold=100,
    )
    calls = []
    original_score = real_score_message

    def record_score(reference, message):
        calls.append((reference, message))
        return original_score(reference, message)

    monkeypatch.setattr(
        live_session_module, "score_message", record_score, raising=False
    )
    session = LiveSession(request, directory=tmp_path)
    try:
        session.resume()
        assert [event["message"] for event in session.step()] == ["Database failed"]
        assert len(calls) == 1
        assert session.has_pending

        snapshot = session.snapshot()
        try:
            assert snapshot.conditions is request
            assert snapshot.conditions.similar_to == "Database failed"
            assert snapshot.conditions.similarity_threshold == 100
            assert snapshot.cutoff == 1
            assert len(calls) == 1
        finally:
            snapshot.release()

        session.pause()
        offset = session.offset
        with source.open("ab") as stream:
            stream.write(_log("Database failed"))
        assert session.step() == []
        assert session.offset == offset
        assert len(calls) == 1

        session.resume(request)
        assert [event["message"] for event in session.step()] == ["Database failed"]
        assert len(calls) == 2
        assert session.step() == []
        assert len(calls) == 2

        assert [event["message"] for event in session.end()] == ["Database failed"]
        assert session.end() == []
        assert len(calls) == 3
        assert session.matched == 3
        assert calls == [("Database failed", "Database failed")] * 3
    finally:
        session.close()


def test_changed_live_similarity_conditions_require_reanalysis(tmp_path):
    source = tmp_path / "app.log"
    source.write_bytes(_log("Database failed"))
    request = LiveAnalysisRequest(
        str(source), levels=None, similar_to="Database failed", similarity_threshold=80
    )
    session = LiveSession(request, directory=tmp_path)
    try:
        session.resume(request)
        assert session.step() == []
        session.pause()
        offset = session.offset

        for changed in (
            replace(request, similar_to="Connection failed"),
            replace(request, similarity_threshold=90),
        ):
            with pytest.raises(ValueError, match="條件"):
                session.resume(changed)
            assert session.state == "paused"
            assert session.offset == offset

        session.resume(request)
        assert session.step() == []
        assert session.offset == offset
    finally:
        session.close()


def test_none_reference_disables_similarity_for_live_request(tmp_path, monkeypatch):
    source = tmp_path / "app.log"
    source.write_bytes(_log("Unrelated event") + _log("another event"))
    request = LiveAnalysisRequest(str(source), levels=None, similar_to=None)

    def unexpected_score(*_args):
        pytest.fail("disabled similarity conditions must not score events")

    monkeypatch.setattr(live_session_module, "score_message", unexpected_score)
    session = LiveSession(request, directory=tmp_path)
    try:
        session.resume()
        assert [event["message"] for event in session.step()] == ["Unrelated event"]
    finally:
        session.close()


@pytest.mark.parametrize(
    ("reference", "message", "threshold", "expected"),
    [
        ("Database failed", "Database failed", 100, True),
        ("Database failed", "database failed", 100, False),
        ("Database failed", "", 0, True),
        ("Database failed", "unrelated", 100, False),
    ],
)
def test_similarity_threshold_is_case_sensitive_and_includes_endpoints(
    tmp_path, reference, message, threshold, expected
):
    source = tmp_path / "app.log"
    source.touch()
    request = LiveAnalysisRequest(
        str(source),
        levels=None,
        similar_to=reference,
        similarity_threshold=threshold,
    )
    session = LiveSession(request, directory=tmp_path)
    entry = {
        "level": "ERROR",
        "timestamp": "2026-10-31 12:00:00.000",
        "full_text": message,
        "message": message,
        "message_body": "",
        "stacktrace": "Database failed in an unrelated stack trace",
        "mdc": {"traceId": "Database failed"},
    }
    try:
        assert session._matches(entry) is expected
    finally:
        session.close()


def test_similarity_uses_message_body_but_not_stacktrace_or_mdc(tmp_path):
    source = tmp_path / "app.log"
    source.touch()
    request = LiveAnalysisRequest(
        str(source),
        levels=None,
        similar_to="Database failed timeout",
        similarity_threshold=100,
    )
    session = LiveSession(request, directory=tmp_path)
    try:
        body_match = {
            "level": "ERROR",
            "timestamp": "2026-10-31 12:00:00.000",
            "full_text": "Database failed timeout",
            "message": "Database failed",
            "message_body": "timeout",
            "stacktrace": "RuntimeError: different stack",
            "mdc": {"traceId": "other"},
        }
        stacktrace_only = body_match | {
            "full_text": "unavailable\nDatabase failed",
            "message": "unavailable",
            "message_body": "",
            "stacktrace": "Database failed",
            "mdc": {"traceId": "Database failed"},
        }

        assert session._matches(body_match) is True
        assert session._matches(stacktrace_only) is False
    finally:
        session.close()


def test_ignore_case_applies_to_keyword_but_not_similarity_score(tmp_path):
    source = tmp_path / "app.log"
    source.touch()
    request = LiveAnalysisRequest(
        str(source),
        keyword="DATABASE",
        ignore_case=True,
        levels=None,
        similar_to="Database failed",
        similarity_threshold=100,
    )
    session = LiveSession(request, directory=tmp_path)
    entry = {
        "level": "ERROR",
        "timestamp": "2026-10-31 12:00:00.000",
        "full_text": "database failed",
        "message": "database failed",
        "message_body": "",
    }
    try:
        assert session._matches(entry) is False
    finally:
        session.close()
