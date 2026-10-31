"""追蹤原文使用保存的事件，不依賴仍可能變動的來源檔。"""

import pytest

from log_analyzer.application.analysis.service import AnalysisRequest
from log_analyzer.application.live.preview import read_tracking_context
from log_analyzer.application.live.session import LiveSession


def test_tracking_preview_survives_deleted_source_and_session_close(tmp_path):
    """來源與工作階段釋放後，快照仍能顯示完整事件及原始行號。"""
    source = tmp_path / "app.log"
    raw = "2026-10-31 12:00:00.000 [main] ERROR sample.App - 中文錯誤\n  at sample.App.run(App.java:8)\n"
    source.write_text(raw + "2026-10-31 12:00:01.000 [main] INFO sample.App - next\n", encoding="utf-8")
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    session.resume()
    session.step()
    snapshot = session.snapshot()
    session.close()
    source.unlink()
    try:
        preview = read_tracking_context(snapshot, {"sequence": 1, "generation": 0})
        assert preview.warning is None
        assert preview.match_line == 1
        assert preview.lines == tuple(enumerate(raw.splitlines(), start=1))
        assert "世代 0" in preview.path_line
        assert "序號 1" in preview.path_line
    finally:
        snapshot.release()


@pytest.mark.parametrize("coordinate", [
    {"sequence": 1, "generation": 999},
    {"sequence": 2, "generation": 0},
    {"sequence": 0, "generation": 0},
    {"sequence": True, "generation": 0},
    {"sequence": "1", "generation": 0},
    {"sequence": 1},
], ids=["wrong-generation", "beyond-cutoff", "zero-sequence", "boolean-sequence", "string-sequence", "missing-generation"])
def test_tracking_preview_rejects_invalid_or_newer_coordinates(tmp_path, coordinate):
    """拒絕不屬於快照的座標，不把新世代或較新資料當作原文。"""
    source = tmp_path / "app.log"
    line = "2026-10-31 12:00:00.000 [main] ERROR sample.App - event\n"
    source.write_text(line * 2, encoding="utf-8")
    session = LiveSession(AnalysisRequest(str(source)), directory=tmp_path)
    try:
        session.resume()
        session.step()
        snapshot = session.snapshot()
        try:
            with source.open("a", encoding="utf-8") as stream:
                stream.write(line)
            session.step()
            preview = read_tracking_context(snapshot, coordinate)
            assert preview.warning
            assert preview.lines == ()
        finally:
            snapshot.release()
    finally:
        session.close()
