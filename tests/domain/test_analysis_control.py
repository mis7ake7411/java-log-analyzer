from __future__ import annotations

from dataclasses import FrozenInstanceError
from importlib import import_module, util
from pathlib import Path

import pytest

from log_analyzer.application.analysis import service as analysis_service
from log_analyzer.application.analysis.service import AnalysisRequest, analyze_logs
from log_analyzer.domain import parser
from log_analyzer.domain.parser import ParseOptions, parse_log_directory


def _analysis_control_module():
    module_name = "log_analyzer.domain.analysis_control"
    assert util.find_spec(module_name) is not None, "analysis control types should exist"
    return import_module(module_name)


def _write_log(path: Path, lines: int = 1) -> None:
    path.write_text(
        "".join(
            f"2026-10-24 12:00:{index % 60:02d}.000 [main] ERROR com.test.App - Event {index}\n"
            for index in range(lines)
        ),
        encoding="utf-8",
    )


def test_analysis_progress_is_immutable_and_control_raises_a_distinct_cancel_error():
    """情境：進度快照應不可變且取消使用獨立例外型別。"""
    control_module = _analysis_control_module()
    progress = control_module.AnalysisProgress(
        phase="scanning",
        filename="app.log",
        files_completed=0,
        files_total=1,
        lines_processed=12,
    )
    control = control_module.AnalysisControl()

    with pytest.raises(FrozenInstanceError):
        progress.phase = "sorting"

    control.cancel()
    with pytest.raises(control_module.AnalysisCancelled):
        control.raise_if_cancelled()


def test_parser_cancels_on_unmatched_lines_and_cleans_grouping_temporary_data(tmp_path, monkeypatch):
    """情境：未匹配行也應檢查取消並清理分組暫存。"""
    control_module = _analysis_control_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    path = logs / "app.log"
    path.write_text(
        "2026-10-24 12:00:00.000 [main] ERROR com.test.App - First\n"
        + "unmatched continuation\n" * 400,
        encoding="utf-8",
    )
    progress_events = []
    temporary_paths: list[Path] = []
    real_temporary_directory = parser.tempfile.TemporaryDirectory

    def track_temporary_directory(*args, **kwargs):
        temporary_directory = real_temporary_directory(*args, **kwargs)
        temporary_paths.append(Path(temporary_directory.name))
        return temporary_directory

    monkeypatch.setattr(parser.tempfile, "TemporaryDirectory", track_temporary_directory)
    control = None

    def cancel_during_scan(progress):
        progress_events.append(progress)
        if progress.phase == "scanning" and progress.lines_processed >= 100:
            control.cancel()

    control = control_module.AnalysisControl(progress_callback=cancel_during_scan)

    with pytest.raises(control_module.AnalysisCancelled):
        parse_log_directory(str(logs), ParseOptions(levels=None), control=control)

    assert any(event.filename == "app.log" and event.lines_processed >= 100 for event in progress_events)
    assert temporary_paths
    assert all(not path.exists() for path in temporary_paths)


def test_parser_removes_partially_persisted_store_when_cancelled(tmp_path, monkeypatch):
    """情境：保存明細途中取消應刪除未完成的儲存器。"""
    control_module = _analysis_control_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    _write_log(logs / "app.log", lines=3)
    persisted_paths: list[Path] = []
    real_named_temporary_file = parser.tempfile.NamedTemporaryFile

    def track_named_temporary_file(*args, **kwargs):
        temporary_file = real_named_temporary_file(*args, **kwargs)
        persisted_paths.append(Path(temporary_file.name))
        return temporary_file

    monkeypatch.setattr(parser.tempfile, "NamedTemporaryFile", track_named_temporary_file)
    control = None

    def cancel_during_persistence(progress):
        if progress.phase == "persisting":
            control.cancel()

    control = control_module.AnalysisControl(progress_callback=cancel_during_persistence)

    with pytest.raises(control_module.AnalysisCancelled):
        parse_log_directory(str(logs), ParseOptions(levels=None), control=control)

    assert persisted_paths
    assert all(not path.exists() for path in persisted_paths)


def test_analysis_releases_persisted_store_when_summary_is_cancelled(tmp_path, monkeypatch):
    """情境：摘要計算取消時分析服務應釋放已保存明細。"""
    control_module = _analysis_control_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    _write_log(logs / "app.log", lines=10)
    stores = []
    real_parse = analysis_service.parse_log_directory

    def capture_store(*args, **kwargs):
        counts, matched_logs = real_parse(*args, **kwargs)
        stores.append(matched_logs)
        return counts, matched_logs

    monkeypatch.setattr(analysis_service, "parse_log_directory", capture_store)
    control = None

    def cancel_summary(progress):
        if progress.phase == "summarizing":
            control.cancel()

    control = control_module.AnalysisControl(progress_callback=cancel_summary)

    with pytest.raises(control_module.AnalysisCancelled):
        analyze_logs(AnalysisRequest(path=str(logs)), control=control)

    assert stores
    assert all(not Path(store.path).exists() for store in stores)


def test_parser_throttles_progress_notifications_without_skipping_line_checkpoints(tmp_path):
    """情境：進度通知應節流但不得省略逐行取消檢查。"""
    control_module = _analysis_control_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    line_count = 1000
    _write_log(logs / "app.log", lines=line_count)
    progress_events = []
    control = control_module.AnalysisControl(progress_callback=progress_events.append)

    _counts, matched_logs = parse_log_directory(
        str(logs), ParseOptions(levels=None), control=control
    )
    try:
        assert len(progress_events) < line_count // 10
        assert progress_events[-1].lines_processed == line_count
    finally:
        matched_logs.release()
