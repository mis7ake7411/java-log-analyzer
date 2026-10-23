import json
from collections import Counter
from pathlib import Path

import pytest

import log_analyzer.application.analysis_service as analysis_service
from log_analyzer.domain.parser import MatchedLogsStore


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


def test_analyze_logs_does_not_validate_or_create_export_paths(tmp_path, monkeypatch):
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


def test_captured_logs_can_be_exported_repeatedly_after_source_is_removed(tmp_path, monkeypatch):
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
