"""重新分析來源只在新結果準備成功後替換舊工作階段。"""

import asyncio
import os

import pytest

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.rebuild import prepare_replacement as rebuild_session
from log_analyzer.application.live.session import LiveSession


def _log(message):
    return f"2026-10-31 12:00:00.000 [main] ERROR com.test - {message}\n".encode()


def _paused_session(source, directory):
    session = LiveSession(AnalysisRequest(str(source)), directory=directory)
    session.resume()
    session.step()
    session.pause()
    return session


def test_rebuild_replaces_only_on_success_and_keeps_new_tail_pending(tmp_path):
    """新來源準備成功前後皆保留舊階段，交由介面原子替換後釋放。"""
    source = tmp_path / "app.log"
    source.write_bytes(_log("old") + _log("old-tail"))
    old = _paused_session(source, tmp_path)
    retained = old.snapshot()
    source.write_bytes(_log("new") + _log("new-tail"))
    new = asyncio.run(rebuild_session(old, old.conditions, directory=tmp_path))
    try:
        assert new.state == "paused"
        assert new.session_id != old.session_id
        assert [e["message"] for e in new.iter_page(0, 10)] == ["new"]
        assert new.pending["message"] == "new-tail"
        assert [e["message"] for e in retained.events] == ["old"]
        assert old.state == "paused"
        assert old.matched == 1
    finally:
        retained.release()
        new.close()
        old.close()


def test_rebuild_validation_failure_retains_old_position_and_results(tmp_path):
    """新來源無效時原結果與位移仍可接續，不能先清空舊階段。"""
    source = tmp_path / "app.log"
    source.write_bytes(_log("old") + _log("tail"))
    old = _paused_session(source, tmp_path)
    position = old.offset
    try:
        with pytest.raises(FileNotFoundError):
            asyncio.run(rebuild_session(old, AnalysisRequest(str(tmp_path / "missing.log")), directory=tmp_path))
        assert old.state == "paused"
        assert old.offset == position
        assert old.matched == 1
        old.resume()
        assert old.step() == []
    finally:
        old.close()


def test_rebuild_requires_safe_pause_before_replacing(tmp_path):
    """未交接完的讀取不能直接重建，避免兩個來源讀取者。"""
    source = tmp_path / "app.log"
    source.write_bytes(_log("old"))
    old = _paused_session(source, tmp_path)
    old.resume()
    try:
        with pytest.raises(RuntimeError, match="暫停"):
            asyncio.run(rebuild_session(old, old.conditions, directory=tmp_path))
        assert old.state == "tracking"
    finally:
        old.close()


def test_rebuild_does_not_chase_continuously_growing_source(tmp_path, monkeypatch):
    """重建固定初始讀取截止點，不因來源持續追加而永遠無法完成。"""
    source = tmp_path / "app.log"
    source.write_bytes(_log("old") + _log("tail"))
    old = _paused_session(source, tmp_path)
    original_step = LiveSession.step
    reads = []

    def append_after_step(self, max_bytes=65536):
        result = original_step(self, max_bytes)
        reads.append(self.offset)
        with source.open("ab") as stream:
            stream.write(_log("appended"))
        assert len(reads) <= 3, "重建不應無限追讀新內容"
        return result

    monkeypatch.setattr(LiveSession, "step", append_after_step)
    new = asyncio.run(rebuild_session(old, old.conditions, directory=tmp_path))
    try:
        assert new.state == "paused"
        assert new.offset < source.stat().st_size
        assert new.backlog > 0
    finally:
        new.close()
        old.close()


def test_rebuild_cancel_retains_old_session_and_cleans_candidate(tmp_path, monkeypatch):
    """取消準備中的新階段時，舊結果仍在且候選暫存不殘留。"""
    source = tmp_path / "app.log"
    source.write_bytes(_log("old") + _log("tail"))
    old = _paused_session(source, tmp_path)
    prior_files = set(tmp_path.iterdir())
    original_step = LiveSession.step

    def cancel_after_step(self, max_bytes=65536):
        result = original_step(self, max_bytes)
        asyncio.current_task().cancel()
        return result

    monkeypatch.setattr(LiveSession, "step", cancel_after_step)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(rebuild_session(old, old.conditions, directory=tmp_path))
        assert old.state == "paused"
        assert old.matched == 1
        assert set(tmp_path.iterdir()) == prior_files
    finally:
        old.close()


def _replace_open_source(replacement, source):
    """Windows 的 MoveFileEx 無法覆蓋開啟中的目標，改用原生 ReplaceFileW。"""
    if os.name != "nt":
        replacement.replace(source)
        return
    import ctypes
    from ctypes import wintypes

    replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace_file.argtypes = (
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID,
    )
    replace_file.restype = wintypes.BOOL
    if not replace_file(str(source), str(replacement), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


@pytest.mark.parametrize("rotation", ["rename-create", "atomic-replace"])
def test_rebuild_rotation_during_preparation_keeps_old_result(tmp_path, monkeypatch, rotation):
    """初始讀取中來源換世代時拒絕發布混合結果，並清理候選暫存。"""
    source = tmp_path / "app.log"
    source.write_bytes(_log("old") + _log("tail"))
    old = _paused_session(source, tmp_path)
    prior_databases = set(tmp_path.glob("*.sqlite3"))
    original_step = LiveSession.step

    def replace_after_read(self, max_bytes=65536):
        events = original_step(self, max_bytes)
        replacement = tmp_path / "replacement.log"
        replacement.write_bytes(_log("different"))
        if rotation == "rename-create":
            source.rename(tmp_path / "old.log")
            replacement.rename(source)
        else:
            _replace_open_source(replacement, source)
        return events

    monkeypatch.setattr(LiveSession, "step", replace_after_read)
    try:
        with pytest.raises(OSError, match="來源"):
            asyncio.run(rebuild_session(old, old.conditions, directory=tmp_path))
        assert old.state == "paused"
        assert [e["message"] for e in old.iter_page(0, 10)] == ["old"]
        assert set(tmp_path.glob("*.sqlite3")) == prior_databases
    finally:
        old.close()


def test_rebuild_read_failure_does_not_publish_partial_new_result(tmp_path, monkeypatch):
    """新階段部分讀取後失敗，也不能取代原本可用的結果。"""
    source = tmp_path / "app.log"
    source.write_bytes(_log("old") + _log("tail"))
    old = _paused_session(source, tmp_path)
    original_step = LiveSession.step

    def fail_after_read(self, max_bytes=65536):
        original_step(self, max_bytes)
        raise OSError("source read failed")

    monkeypatch.setattr(LiveSession, "step", fail_after_read)
    try:
        with pytest.raises(OSError, match="source read failed"):
            asyncio.run(rebuild_session(old, old.conditions, directory=tmp_path))
        assert old.state == "paused"
        assert old.matched == 1
        assert len(list(tmp_path.glob("*.sqlite3"))) == 1
    finally:
        old.close()
