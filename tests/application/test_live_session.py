"""追蹤工作階段的暫停、固定條件與快照行為。"""

from dataclasses import replace
from datetime import datetime

import pytest

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.session import LiveSession


def log(message, level="ERROR"):
    return f"2026-10-31 12:00:00.000 [main] {level} com.test - {message}\n".encode()


def test_pause_retains_pending_and_resume_reads_only_append(tmp_path):
    """暫停期間不讀新增 bytes，恢復後續行仍歸屬原待完成事件。"""
    source = tmp_path / "app.log"
    source.write_bytes(log("first") + log("pending"))
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    try:
        session.resume()
        assert [e["message"] for e in session.step()] == ["first"]
        assert session.completed == session.matched == 1
        session.pause()
        position = session.offset
        with source.open("ab") as stream:
            stream.write(b"java.lang.Exception: detail\n" + log("next"))
        assert session.step() == []
        assert session.offset == position
        assert session.pending["message"] == "pending"
        session.resume()
        events = session.step()
        assert [e["message"] for e in events] == ["pending"]
        assert "Exception: detail" in events[0]["raw_text"]
        assert session.completed == session.matched == 2
        assert session.step() == []
        assert [e["message"] for e in session.end()] == ["next"]
        assert session.end() == []
    finally:
        session.close()


def test_snapshot_keeps_cutoff_raw_and_original_conditions_after_source_deleted(tmp_path):
    """快照只納入完成命中事件；不因來源消失或後續接收而變動。"""
    source = tmp_path / "app.log"
    source.write_bytes(log("excluded", "INFO") + log("kept") + log("pending"))
    request = AnalysisRequest(str(source), levels=("ERROR",))
    session = LiveSession(request, directory=tmp_path)
    try:
        session.resume()
        session.step()
        snapshot = session.snapshot()
        try:
            assert snapshot.conditions == request
            assert snapshot.completed == 2
            assert snapshot.cutoff == 1
            assert snapshot.has_pending
            session.pause()
            with source.open("ab") as stream:
                stream.write(log("later"))
            session.resume()
            session.step()
            session.pause()
            source.unlink()
            assert [e["message"] for e in snapshot.events] == ["kept"]
            assert list(snapshot.events)[0]["raw_text"] == log("kept").decode()
            assert session.matched == 2
        finally:
            snapshot.release()
    finally:
        session.close()


def test_changed_conditions_require_reanalysis_without_consuming_source(tmp_path):
    """編輯條件後拒絕直接恢復；拒絕時不更動暫停位置。"""
    source = tmp_path / "app.log"
    source.write_bytes(log("first"))
    request = AnalysisRequest(str(source))
    session = LiveSession(request, directory=tmp_path)
    try:
        session.resume()
        session.step()
        session.pause()
        offset = session.offset
        with pytest.raises(ValueError, match="重新分析來源"):
            session.resume(replace(request, keyword="changed"))
        assert session.state == "paused"
        assert session.offset == offset
        session.resume(request)
        assert session.step() == []
    finally:
        session.close()


def test_keyword_counts_completed_separately_from_matches(tmp_path):
    """未命中也算完成解析，暫時 EOF 的最後事件不提前統計。"""
    source = tmp_path / "app.log"
    source.write_bytes(log("keep me") + log("discard") + log("pending"))
    session = LiveSession(AnalysisRequest(str(source), keyword="KEEP", ignore_case=True), directory=tmp_path)
    try:
        session.resume()
        session.step()
        assert session.completed == 2
        assert session.matched == 1
        assert session.level_counts == {"ERROR": 1}
        assert session.received_bytes == source.stat().st_size
    finally:
        session.close()


def test_live_rejects_directory_before_creating_storage(tmp_path):
    """第一版只追蹤單一檔案，資料夾維持批次功能。"""
    with pytest.raises(ValueError, match="單一"):
        LiveSession(AnalysisRequest(str(tmp_path)), directory=tmp_path)


def test_naive_time_bounds_use_same_inclusive_semantics_as_batch(tmp_path):
    """UI 未附時區的起訖值應與批次分析同樣正規化，包含邊界。"""
    source = tmp_path / "app.log"
    source.write_bytes(log("boundary") + log("pending"))
    boundary = datetime(2026, 10, 31, 12)
    session = LiveSession(AnalysisRequest(str(source), start_dt=boundary, end_dt=boundary), directory=tmp_path)
    try:
        session.resume()
        assert [e["message"] for e in session.step()] == ["boundary"]
    finally:
        session.close()


def test_invalid_time_window_is_rejected_before_reading(tmp_path):
    """反向時間範圍應直接提示，不建立永遠零筆的追蹤。"""
    source = tmp_path / "app.log"
    source.write_bytes(log("first"))
    with pytest.raises(ValueError, match="時間"):
        LiveSession(AnalysisRequest(str(source), start_dt=datetime(2026, 11, 1),
                                    end_dt=datetime(2026, 10, 1)), directory=tmp_path)


def test_storage_failure_stops_reception_and_retains_prior_events(tmp_path, monkeypatch):
    """寫入失敗後不能繼續消費來源，已保存的快照仍可讀。"""
    source = tmp_path / "app.log"
    source.write_bytes(log("first") + log("pending"))
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    try:
        session.resume()
        session.step()
        original_append = session._store.append

        def fail_append(*args, **kwargs):
            raise OSError("disk is full")

        monkeypatch.setattr(session._store, "append", fail_append)
        with source.open("ab") as stream:
            stream.write(log("tail"))
        with pytest.raises(OSError, match="disk is full"):
            session.step()
        assert session.state == "error"
        position = session.offset
        assert session.step() == []
        assert session.offset == position
        snapshot = session.snapshot()
        try:
            assert [e["message"] for e in snapshot.events] == ["first"]
        finally:
            snapshot.release()
        monkeypatch.setattr(session._store, "append", original_append)
        # 結束錯誤階段不能再補寫尾筆、掩蓋先前失敗或越過遺失的事件。
        assert session.end() == []
        assert session.matched == 1
        assert session.error == "disk is full"
    finally:
        session.close()


def test_partial_utf8_header_is_reported_as_pending_and_resumes(tmp_path):
    """首行尚未完成時也要提示接收中，跨暫停的中文字不可損壞。"""
    source = tmp_path / "app.log"
    content = log("中文")
    cut = content.index("中".encode()) + 1
    source.write_bytes(content[:cut])
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    try:
        session.resume()
        assert session.step() == []
        session.pause()
        snapshot = session.snapshot()
        try:
            assert snapshot.has_pending
            assert snapshot.cutoff == 0
            assert session.pending is None
        finally:
            snapshot.release()
        with source.open("ab") as stream:
            stream.write(content[cut:] + log("tail"))
        session.resume()
        events = session.step()
        assert [event["message"] for event in events] == ["中文"]
        assert events[0]["raw_text"] == content.decode()
    finally:
        session.close()


def test_empty_source_is_not_reported_as_pending(tmp_path):
    """尚未讀到任何 bytes 不能因解碼器的 BOM 初始狀態誤報接收中。"""
    source = tmp_path / "empty.log"
    source.touch()
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    try:
        assert not session.has_pending
        session.resume()
        assert session.step() == []
        assert not session.has_pending
    finally:
        session.close()
