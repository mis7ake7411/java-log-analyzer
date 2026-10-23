import csv
import hashlib
import importlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from log_analyzer.application.analysis_service import (
    AnalysisRequest,
    ResultExportOptions,
    analyze_logs,
    export_analysis_result,
)
from log_analyzer.infrastructure.exporting.fields import EXPORT_FIELD_LABELS
from log_analyzer.version import get_package_version


def _export_evidence_manifest():
    module_name = "log_analyzer.application.evidence_manifest"
    assert importlib.util.find_spec(module_name) is not None, "manifest exporter is missing"
    return importlib.import_module(module_name).export_evidence_manifest


def _analyzed_export(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    source = logs / "app.log"
    source.write_text(
        "2026-06-07 12:00:00.000 [main] ERROR com.test.App - First FAILURE\n"
        "2026-06-07 12:01:00.000 [main] WARN  com.test.App - Second failure\n"
        "2026-06-07 12:02:00.000 [main] INFO  com.test.App - Ignored\n",
        encoding="utf-8",
    )
    request = AnalysisRequest(
        path=str(logs),
        start_dt=datetime(2026, 6, 7, 11, 59),
        end_dt=datetime(2026, 6, 7, 12, 1),
        keyword="FAILURE",
        ignore_case=True,
        sort_by="time",
        log_pattern=None,
        levels=("ERROR", "WARN"),
    )
    result = analyze_logs(request)
    report_options = ResultExportOptions(
        output_path=str(tmp_path / "selected-report.csv"),
        fmt="csv",
        fields=("message", "level"),
        max_export_bytes=50_000,
        split_by_level=True,
    )
    report_files = export_analysis_result(result, report_options)
    return result, report_options, report_files, source, logs


def test_manifest_captures_frozen_analysis_reports_and_source_hashes(tmp_path):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, report_files, source, logs = _analyzed_export(tmp_path)

    manifest_path = export_evidence_manifest(result, report_options, report_files)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert Path(manifest_path) == tmp_path / "selected-report_manifest.json"
    assert manifest["schema_version"] == 1
    assert manifest["tool"] == {
        "name": "java-log-analyzer",
        "version": get_package_version(),
    }
    assert manifest["analysis"]["filters"] == {
        "start_dt": "2026-06-07T11:59:00",
        "end_dt": "2026-06-07T12:01:00",
        "levels": ["ERROR", "WARN"],
        "pattern": None,
        "keyword": "FAILURE",
        "ignore_case": True,
        "sort_by": "time",
    }
    assert manifest["analysis"]["counts"] == {"ERROR": 1, "WARN": 1}
    assert manifest["analysis"]["matched_groups"] == 2
    assert manifest["analysis"]["matched_occurrences"] == 2
    assert manifest["analysis"]["diagnostics"] == {
        "files_scanned": 1,
        "parsed_entries": 3,
        "after_time": 2,
        "after_level": 2,
        "after_keyword": 2,
    }
    assert manifest["export"] == {
        "fields": ["level", "message"],
        "format": "csv",
        "split_by_level": True,
        "max_export_bytes": 50_000,
    }
    assert {Path(path).name for path in report_files} == {
        "selected-report_summary.csv",
        "selected-report_ERROR.csv",
        "selected-report_WARN.csv",
    }
    selected_headers = [EXPORT_FIELD_LABELS["level"], EXPORT_FIELD_LABELS["message"]]
    for report_path in report_files[1:]:
        with Path(report_path).open(encoding="utf-8-sig", newline="") as file_handle:
            assert selected_headers in list(csv.reader(file_handle))
    assert {entry["filename"] for entry in manifest["outputs"]} == {
        Path(path).name for path in report_files
    }
    for entry, report_path in zip(manifest["outputs"], report_files, strict=True):
        report_bytes = Path(report_path).read_bytes()
        assert entry["size"] == len(report_bytes)
        assert entry["sha256"] == hashlib.sha256(report_bytes).hexdigest()

    source_entry = manifest["sources"][0]
    source_snapshot = result.source_states["app.log"]
    assert source_entry["filename"] == "app.log"
    assert source_entry["scanned"] == dict(source_snapshot)
    assert source_entry["verification_basis"] == "size_mtime_ns"
    assert source_entry["verification_status"] == "verified"
    assert source_entry["sha256_at_export"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "snapshot" in manifest["limitations"].lower()

    serialized = Path(manifest_path).read_text(encoding="utf-8")
    assert str(logs.resolve()) not in serialized
    assert str(tmp_path.resolve()) not in serialized
    assert "First FAILURE" not in serialized
    assert "Second failure" not in serialized


def test_manifest_uses_file_source_root_and_reports_verified_changed_and_missing(tmp_path):
    export_evidence_manifest = _export_evidence_manifest()
    logs = tmp_path / "logs"
    logs.mkdir()
    source = logs / "chosen.txt"
    source.write_text(
        "2026-06-07 12:00:00.000 INFO  [main] com.test.App: selected\n",
        encoding="utf-8",
    )
    (logs / "sibling.log").write_text(
        "2026-06-07 12:00:01.000 ERROR [main] com.test.App: sibling\n",
        encoding="utf-8",
    )
    try:
        result = analyze_logs(
            AnalysisRequest(
                path=str(source),
                log_pattern="%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{0}: %msg%n",
            )
        )
    except (FileNotFoundError, ValueError) as error:
        pytest.fail(f"a supported single log file should be analyzable: {error}")
    options = ResultExportOptions(output_path=str(tmp_path / "selected.csv"), fmt="csv")

    try:
        assert result.input_path == str(logs.resolve())
        assert result.analysis_request.path == str(source.resolve())
        assert set(result.source_states) == {"chosen.txt"}

        manifest_path = export_evidence_manifest(result, options, ())
        verified = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert [entry["filename"] for entry in verified["sources"]] == ["chosen.txt"]
        assert verified["sources"][0]["verification_status"] == "verified"

        source.write_text(
            "2026-06-07 12:00:00.000 INFO  [main] com.test.App: changed after analysis\n",
            encoding="utf-8",
        )
        manifest_path = export_evidence_manifest(result, options, ())
        changed = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert changed["sources"][0]["verification_status"] == "changed"

        source.unlink()
        manifest_path = export_evidence_manifest(result, options, ())
        missing = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert missing["sources"][0]["verification_status"] == "missing"
    finally:
        result.matched_logs.release()


def test_source_changes_and_scan_failures_are_recorded_without_hashes(tmp_path):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, report_files, source, logs = _analyzed_export(tmp_path)
    scanned = dict(result.source_states["app.log"])

    source.write_text(source.read_text(encoding="utf-8") + "changed after scan\n", encoding="utf-8")
    result.source_states.update(
        {
            "deleted.log": dict(scanned),
            "unstable.log": {**scanned, "stable": False},
            "../outside.log": dict(scanned),
            str(tmp_path / "absolute-secret.log"): dict(scanned),
        }
    )
    (tmp_path / "absolute-secret.log").write_text("must not be read", encoding="utf-8")

    manifest_path = export_evidence_manifest(result, report_options, report_files)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    sources = {entry["filename"]: entry for entry in manifest["sources"]}

    assert sources["app.log"]["verification_status"] == "changed"
    assert sources["deleted.log"]["verification_status"] == "missing"
    assert sources["unstable.log"]["verification_status"] == "unstable"
    assert sources["../outside.log"]["verification_status"] == "unsafe"
    assert sources["absolute-secret.log"]["verification_status"] == "unsafe"
    for entry in sources.values():
        assert "sha256_at_export" not in entry
    serialized = Path(manifest_path).read_text(encoding="utf-8")
    assert str(logs.resolve()) not in serialized
    assert str(tmp_path.resolve()) not in serialized


def test_unreadable_source_is_reported_without_failing_manifest(tmp_path, monkeypatch):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, report_files, source, _logs = _analyzed_export(tmp_path)
    original_open = Path.open

    def deny_source_open(path, *args, **kwargs):
        if path.resolve() == source.resolve():
            raise PermissionError("denied for test")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_source_open)

    manifest_path = export_evidence_manifest(result, report_options, report_files)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert manifest["sources"][0]["verification_status"] == "unreadable"
    assert "sha256_at_export" not in manifest["sources"][0]


def test_source_change_during_hash_is_not_marked_verified(tmp_path, monkeypatch):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, report_files, source, _logs = _analyzed_export(tmp_path)
    original_open = Path.open

    class MutatingReader:
        def __init__(self, file_handle):
            self.file_handle = file_handle
            self.mutated = False

        def __enter__(self):
            self.file_handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.file_handle.__exit__(*args)

        def read(self, size=-1):
            block = self.file_handle.read(size)
            if not block and not self.mutated:
                with open(source, "ab") as file_handle:
                    file_handle.write(b"changed while hashing\n")
                self.mutated = True
            return block

    def mutate_after_source_read(path, *args, **kwargs):
        file_handle = original_open(path, *args, **kwargs)
        if path == source:
            return MutatingReader(file_handle)
        return file_handle

    monkeypatch.setattr(Path, "open", mutate_after_source_read)

    manifest_path = export_evidence_manifest(result, report_options, report_files)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert manifest["sources"][0]["verification_status"] == "changed"
    assert "sha256_at_export" not in manifest["sources"][0]


def test_resolved_source_outside_root_is_unsafe_and_never_opened(tmp_path, monkeypatch):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, report_files, _source, logs = _analyzed_export(tmp_path)
    result.source_states["linked.log"] = dict(result.source_states["app.log"])
    outside = tmp_path / "outside.log"
    outside.write_text("outside the selected directory", encoding="utf-8")
    original_resolve = Path.resolve
    original_open = Path.open
    linked = logs / "linked.log"

    def resolve_link(path, *args, **kwargs):
        if path == linked:
            return original_resolve(outside, *args, **kwargs)
        return original_resolve(path, *args, **kwargs)

    def reject_outside_open(path, *args, **kwargs):
        if path == outside:
            raise AssertionError("outside source must not be read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_link)
    monkeypatch.setattr(Path, "open", reject_outside_open)

    manifest_path = export_evidence_manifest(result, report_options, report_files)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    linked_entry = next(entry for entry in manifest["sources"] if entry["filename"] == "linked.log")

    assert linked_entry["verification_status"] == "unsafe"
    assert "sha256_at_export" not in linked_entry


def test_unresolvable_source_root_is_recorded_without_failing_manifest(tmp_path, monkeypatch):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, report_files, _source, _logs = _analyzed_export(tmp_path)
    root = Path(result.analysis_request.path)
    original_resolve = Path.resolve

    def fail_root_resolution(path, *args, **kwargs):
        if path == root:
            raise PermissionError("root access denied")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_root_resolution)
    try:
        manifest_path = export_evidence_manifest(result, report_options, report_files)
    except OSError:
        manifest_path = None

    assert manifest_path is not None, "source root failure must not fail manifest export"
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert manifest["sources"][0]["verification_status"] == "unsafe"
    assert "sha256_at_export" not in manifest["sources"][0]


def test_manifest_requires_frozen_analysis_request(tmp_path):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, _report_files, _source, _logs = _analyzed_export(tmp_path)
    result.analysis_request = None

    with pytest.raises(ValueError, match="frozen analysis request"):
        export_evidence_manifest(result, report_options, [])


def test_manifest_replace_failure_preserves_previous_manifest_and_reports(tmp_path, monkeypatch):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, report_files, _source, _logs = _analyzed_export(tmp_path)
    manifest_path = tmp_path / "selected-report_manifest.json"
    manifest_path.write_text("previous manifest", encoding="utf-8")
    report_bytes = {path: Path(path).read_bytes() for path in report_files}

    import log_analyzer.application.evidence_manifest as evidence_manifest

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(evidence_manifest.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        export_evidence_manifest(result, report_options, report_files)

    assert manifest_path.read_text(encoding="utf-8") == "previous manifest"
    assert {path: Path(path).read_bytes() for path in report_files} == report_bytes
    assert list(tmp_path.glob(".selected-report_manifest.json.*.tmp")) == []


def test_missing_report_fails_without_replacing_existing_manifest(tmp_path):
    export_evidence_manifest = _export_evidence_manifest()
    result, report_options, _report_files, _source, _logs = _analyzed_export(tmp_path)
    manifest_path = tmp_path / "selected-report_manifest.json"
    manifest_path.write_text("previous manifest", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        export_evidence_manifest(result, report_options, [str(tmp_path / "missing.csv")])

    assert manifest_path.read_text(encoding="utf-8") == "previous manifest"
