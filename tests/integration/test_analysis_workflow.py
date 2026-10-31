import json
from collections import Counter
from pathlib import Path

import pytest

import log_analyzer.application.analysis.service as analysis_service
from log_analyzer.domain.parser import MatchedLogsStore
from log_analyzer.application.sources.preview import read_source_context


STANDARD_LOG_PATTERN = "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n"


def _write_log(logs_dir):
    logs_dir.mkdir()
    source = logs_dir / "app.log"
    source.write_text(
        "2026-06-07 12:00:00.000 [worker-7] ERROR com.test.Worker - Request failed\n",
        encoding="utf-8",
    )
    return source


def _request(path, **kwargs):
    return analysis_service.AnalysisRequest(path=str(path), **kwargs)


def _release(result):
    result.matched_logs.release()


@pytest.mark.parametrize("sort_by", ["time", "level"], ids=['sort-by-time', 'sort-by-level'])
def test_dashboard_distributions_follow_occurrences_across_files(tmp_path, sort_by):
    """情境：不同排序下儀表板分布都應依跨檔出現位置統計。"""
    (tmp_path / "first.log").write_text(
        "2026-10-24 10:59:59.999 [worker-a] ERROR Service - Request failed\n"
        "2026-10-24 11:00:00.000 [worker-b] ERROR Service - Request failed\n",
        encoding="utf-8",
    )
    (tmp_path / "second.log").write_text(
        "2026-10-24 11:01:00.000 [worker-b] ERROR Service - Request failed\n"
        "2026-10-24 11:02:00.000 [worker-a] ERROR Service - Other failure\n",
        encoding="utf-8",
    )
    result = analysis_service.analyze_logs(_request(tmp_path, sort_by=sort_by))
    try:
        assert result.matched_groups == 2
        assert result.matched_occurrences == 4
        assert result.dashboard_summary.time_hotspots == (
            ("2026-10-24 11:00", 2, 3), ("2026-10-24 10:00", 1, 1),
        )
        assert result.dashboard_summary.thread_summary == (
            ("worker-a", 2, 2), ("worker-b", 1, 2),
        )
    finally:
        _release(result)


def test_analyze_logs_does_not_validate_or_create_export_paths(tmp_path, monkeypatch):
    """情境：單純分析不得驗證或建立匯出路徑。"""
    logs_dir = tmp_path / "logs"
    _write_log(logs_dir)
    missing_output_dir = tmp_path / "missing-output"

    def reject_output_validation(_path):
        pytest.fail("analysis must not validate an export directory")

    monkeypatch.setattr(analysis_service, "ensure_writable_directory", reject_output_validation)

    result = analysis_service.analyze_logs(_request(logs_dir))
    try:
        assert not missing_output_dir.exists()
        assert result.output_path == ""
        assert result.format_name == "未匯出"
        assert result.exported_files == []
        assert result.dashboard_summary is not None
        assert result.analysis_request.path == str(logs_dir.resolve())
    finally:
        _release(result)


def test_analysis_request_levels_are_copied_to_an_immutable_tuple(tmp_path):
    """情境：分析請求中的 Level 應複製為不可變 tuple。"""
    logs_dir = tmp_path / "logs"
    _write_log(logs_dir)
    levels = ["ERROR"]

    result = analysis_service.analyze_logs(_request(logs_dir, levels=levels))
    try:
        levels.append("INFO")

        assert result.analysis_request.levels == ("ERROR",)
        assert result.selected_levels == ("ERROR",)
    finally:
        _release(result)


def test_analyze_logs_uses_only_a_selected_file_and_keeps_the_preview_root(tmp_path):
    """情境：單檔分析只讀該檔並保留正確原文根目錄。"""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    selected_file = logs_dir / "chosen.LOG"
    selected_file.write_text(
        "2026-06-07 12:00:00.000 INFO  [main] com.test.App: selected\n",
        encoding="utf-8",
    )
    (logs_dir / "sibling.log").write_text(
        "2026-06-07 12:00:01.000 ERROR [main] com.test.App: sibling\n",
        encoding="utf-8",
    )

    try:
        result = analysis_service.analyze_logs(
            _request(selected_file, log_pattern=STANDARD_LOG_PATTERN)
        )
    except (FileNotFoundError, ValueError) as error:
        pytest.fail(f"a supported single log file should be analyzable: {error}")

    try:
        assert result.input_path == str(logs_dir.resolve())
        assert result.analysis_request.path == str(selected_file.resolve())
        assert result.analysis_request.selected_files == ("chosen.LOG",)
        assert result.counts == {"INFO": 1}
        assert set(result.source_states) == {"chosen.LOG"}
        assert result.diagnostics.files_scanned == 1
        preview = read_source_context(
            result.input_path,
            {"filename": "chosen.LOG", "line_number": 1},
            result.source_states["chosen.LOG"],
        )
        assert preview.path_line == f"{selected_file.resolve()}:1"
        assert preview.lines == ((1, "2026-06-07 12:00:00.000 INFO  [main] com.test.App: selected"),)
        assert preview.warning is None
    finally:
        _release(result)


def test_analyze_logs_does_not_expand_frozen_selection_after_new_file_arrives(
    tmp_path, monkeypatch
):
    """情境：開始分析後新增檔案不得擴大固定選取範圍。"""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "chosen.log").write_text(
        "2026-06-07 12:00:00.000 INFO  [main] com.test.App: selected\n",
        encoding="utf-8",
    )
    (logs_dir / "sibling.log").write_text(
        "2026-06-07 12:00:01.000 ERROR [main] com.test.App: sibling\n",
        encoding="utf-8",
    )
    real_parse = analysis_service.parse_log_directory

    def add_file_before_parse(path, options):
        (logs_dir / "arrived.log").write_text(
            "2026-06-07 12:00:02.000 WARN  [main] com.test.App: arrived\n",
            encoding="utf-8",
        )
        return real_parse(path, options)

    monkeypatch.setattr(analysis_service, "parse_log_directory", add_file_before_parse)
    try:
        result = analysis_service.analyze_logs(
            _request(
                logs_dir,
                log_pattern=STANDARD_LOG_PATTERN,
                selected_files=("chosen.log",),
            )
        )
    except TypeError as error:
        pytest.fail(f"analysis requests should preserve selected files: {error}")

    try:
        assert result.counts == {"INFO": 1}
        assert set(result.source_states) == {"chosen.log"}
        assert result.diagnostics.files_scanned == 1
    finally:
        _release(result)


def test_captured_logs_can_be_exported_repeatedly_after_source_is_removed(tmp_path, monkeypatch):
    """情境：來源移除後仍應能重複匯出已保存分析。"""
    logs_dir = tmp_path / "logs"
    source = _write_log(logs_dir)
    real_parse = analysis_service.parse_log_directory
    parse_calls = 0

    def count_parse_calls(path, options):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(path, options)

    monkeypatch.setattr(analysis_service, "parse_log_directory", count_parse_calls)
    result = analysis_service.analyze_logs(_request(logs_dir))
    try:
        source.unlink()

        csv_paths = analysis_service.export_analysis_result(
            result,
            analysis_service.ResultExportOptions(
                output_path=str(tmp_path / "first.csv"),
                fmt="csv",
                fields=("timestamp", "message"),
            ),
        )
        json_paths = analysis_service.export_analysis_result(
            result,
            analysis_service.ResultExportOptions(
                output_path=str(tmp_path / "second.json"),
                fmt="json",
                fields=("level", "message"),
            ),
        )

        assert parse_calls == 1
        assert csv_paths == [str(tmp_path / "first.csv")]
        assert json_paths == [str(tmp_path / "second.json")]
        assert "Request failed" in (tmp_path / "first.csv").read_text(encoding="utf-8")
        assert "com.test.Worker" not in (tmp_path / "first.csv").read_text(encoding="utf-8")
        report = json.loads((tmp_path / "second.json").read_text(encoding="utf-8"))
        assert report["results"] == [{"level": "ERROR", "message": "Request failed"}]
        assert result.output_path == str(tmp_path / "second.json")
        assert result.format_name == "json"
        assert result.exported_files == json_paths
    finally:
        _release(result)


def test_failed_export_preserves_store_and_metadata_for_retry(tmp_path):
    """情境：匯出失敗應保留明細與結果資訊供重試。"""
    logs_dir = tmp_path / "logs"
    _write_log(logs_dir)
    result = analysis_service.analyze_logs(_request(logs_dir))
    store = result.matched_logs
    original_metadata = (result.output_path, result.format_name, list(result.exported_files))
    try:
        with pytest.raises(FileNotFoundError):
            analysis_service.export_analysis_result(
                result,
                analysis_service.ResultExportOptions(
                    output_path=str(tmp_path / "missing-output" / "report.csv"),
                ),
            )

        assert result.matched_logs is store
        assert len(result.matched_logs) == 1
        assert (result.output_path, result.format_name, result.exported_files) == original_metadata

        paths = analysis_service.export_analysis_result(
            result,
            analysis_service.ResultExportOptions(output_path=str(tmp_path / "retry.csv")),
        )
        assert paths == [str(tmp_path / "retry.csv")]
        assert (tmp_path / "retry.csv").exists()
    finally:
        _release(result)


def test_export_rejects_legacy_result_after_details_are_released(tmp_path, monkeypatch):
    """情境：舊結果明細已釋放時應拒絕再次匯出。"""
    logs_dir = tmp_path / "logs"
    _write_log(logs_dir)
    result = analysis_service.run_analysis(
        str(logs_dir),
        str(tmp_path / "initial.csv"),
        None,
        None,
        None,
        False,
        "time",
        "csv",
        include_details=False,
    )
    assert result.matched_groups == 1
    assert result.matched_logs == []
    previous_metadata = (result.output_path, result.format_name, list(result.exported_files))
    retry_path = tmp_path / "retry.csv"
    real_export = analysis_service.export_with_options
    export_calls = []

    def record_export(options):
        export_calls.append(options)
        return real_export(options)

    monkeypatch.setattr(analysis_service, "export_with_options", record_export)

    with pytest.raises(ValueError, match="明細已釋放"):
        analysis_service.export_analysis_result(
            result,
            analysis_service.ResultExportOptions(output_path=str(retry_path)),
        )

    assert export_calls == []
    assert not retry_path.exists()
    assert (result.output_path, result.format_name, result.exported_files) == previous_metadata


def test_legacy_wrapper_releases_store_when_export_fails(tmp_path, monkeypatch):
    """情境：舊一次性分析包裝器匯出失敗時應釋放儲存器。"""
    logs_dir = tmp_path / "logs"
    _write_log(logs_dir)
    real_parse = analysis_service.parse_log_directory
    real_release = MatchedLogsStore.release
    captured_stores = []
    released_stores = []

    def capture_store(path, options):
        counts, store = real_parse(path, options)
        captured_stores.append(store)
        return counts, store

    def record_release(store):
        released_stores.append(store)
        real_release(store)

    monkeypatch.setattr(analysis_service, "parse_log_directory", capture_store)
    monkeypatch.setattr(MatchedLogsStore, "release", record_release)

    with pytest.raises(FileNotFoundError):
        analysis_service.run_analysis(
            str(logs_dir),
            str(tmp_path / "missing-output" / "report.csv"),
            None,
            None,
            None,
            False,
            "time",
            "csv",
        )

    assert len(captured_stores) == 1
    assert captured_stores[0] in released_stores
    assert not Path(captured_stores[0].path).exists()


def test_analyze_logs_releases_store_when_summary_computation_fails(tmp_path, monkeypatch):
    """情境：摘要計算失敗時分析服務應釋放儲存器。"""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    class ReleasableStore(list):
        diagnostics = None
        source_states = {}

        def __init__(self):
            super().__init__([{"count": 1, "level": "INFO"}])
            self.released = False

        def release(self):
            self.released = True

    store = ReleasableStore()
    monkeypatch.setattr(
        analysis_service,
        "parse_log_directory",
        lambda _path, _options: (Counter({"INFO": 1}), store),
    )

    def fail_summary(_matched_logs):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(analysis_service, "aggregate_dashboard_summary", fail_summary)

    with pytest.raises(RuntimeError, match="summary failed"):
        analysis_service.analyze_logs(_request(logs_dir))

    assert store.released is True
