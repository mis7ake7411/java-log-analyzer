import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from log_analyzer.application.analysis.service import (
    AnalysisRequest,
    AnalysisResult,
    ResultExportOptions,
    export_analysis_bundle,
)
from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.infrastructure.exporting import ExportConflictError


def _log(level, index):
    return {
        "count": 1,
        "line_numbers": [index],
        "timestamp": f"2026-06-07 12:00:{index:02d}.000",
        "last_timestamp": f"2026-06-07 12:00:{index:02d}.000",
        "level": level,
        "thread": "main",
        "logger": "com.test.App",
        "filename": "app.log",
        "message": f"{level} message {index}",
        "message_body": f"body {index}",
        "stacktrace": "",
        "occurrences": [],
    }


def _result(input_dir, source_names=("app.log",)):
    input_dir.mkdir(parents=True, exist_ok=True)
    source_states = {}
    for name in source_names:
        source = input_dir / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("source bytes", encoding="utf-8")
        stat = source.stat()
        source_states[name] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "stable": True,
        }
    return AnalysisResult(
        input_path=str(input_dir.resolve()),
        output_path="previous.csv",
        format_name="json",
        keyword="failure",
        ignore_case=True,
        total_logs=2,
        matched_groups=2,
        matched_occurrences=2,
        level_summary=[("ERROR", 1), ("WARN", 1)],
        selected_levels=("ERROR", "WARN"),
        sort_by="time",
        counts=Counter({"ERROR": 1, "WARN": 1}),
        matched_logs=[_log("ERROR", 1), _log("WARN", 2)],
        exported_files=["previous.csv"],
        source_states=source_states,
        diagnostics=ScanDiagnostics(),
        analysis_request=AnalysisRequest(path=str(input_dir.resolve()), levels=("ERROR", "WARN")),
    )


def _snapshot_metadata(result):
    return result.output_path, result.format_name, list(result.exported_files)


def test_export_analysis_bundle_publishes_reports_and_manifest_together(tmp_path):
    """情境：報表與證據清單應由同一次發布流程完成。"""
    result = _result(tmp_path / "logs")
    output = tmp_path / "report.csv"
    options = ResultExportOptions(output_path=str(output), split_by_level=True)

    reports, manifest_path = export_analysis_bundle(result, options)

    assert [Path(path).name for path in reports] == [
        "report_summary.csv",
        "report_ERROR.csv",
        "report_WARN.csv",
    ]
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert {entry["filename"] for entry in manifest["outputs"]} == {
        Path(path).name for path in reports
    }
    assert all(entry["sha256"] == hashlib.sha256(Path(path).read_bytes()).hexdigest()
               for entry, path in zip(manifest["outputs"], reports, strict=True))
    summary = Path(reports[0]).read_text(encoding="utf-8-sig")
    assert all(str(Path(path).resolve()) in summary for path in reports[1:])
    assert ".export-" not in summary
    assert result.output_path == reports[0]
    assert result.format_name == "csv"
    assert result.exported_files == reports
    assert not list(tmp_path.glob(".report.csv.export-*"))


def test_bundle_manifest_failure_leaves_no_new_reports_or_metadata_changes(tmp_path, monkeypatch):
    """情境：清單建立失敗時不得留下新報表或更改結果中繼資料。"""
    import log_analyzer.application.analysis.evidence as evidence_manifest

    result = _result(tmp_path / "logs")
    output = tmp_path / "report.csv"
    options = ResultExportOptions(output_path=str(output), split_by_level=True)
    before = _snapshot_metadata(result)

    def fail_manifest(*_args, **_kwargs):
        raise OSError("simulated manifest render failure")

    monkeypatch.setattr(evidence_manifest, "_write_manifest_file", fail_manifest)

    with pytest.raises(OSError, match="simulated manifest render failure"):
        export_analysis_bundle(result, options)

    assert _snapshot_metadata(result) == before
    assert not list(tmp_path.glob("report*"))
    assert not list(tmp_path.glob(".report.csv.export-*"))


def test_existing_manifest_conflict_aborts_bundle_before_any_report_is_published(tmp_path):
    """情境：只有清單檔名衝突時也應在發布任何報表前中止。"""
    result = _result(tmp_path / "logs")
    output = tmp_path / "report.csv"
    manifest = tmp_path / "report_manifest.json"
    manifest.write_bytes(b"previous manifest")
    options = ResultExportOptions(output_path=str(output), split_by_level=True)
    before = _snapshot_metadata(result)

    with pytest.raises(ExportConflictError) as error:
        export_analysis_bundle(result, options)

    assert error.value.paths == (str(manifest.resolve()),)
    assert manifest.read_bytes() == b"previous manifest"
    assert _snapshot_metadata(result) == before
    assert not list(tmp_path.glob("report_summary.csv"))
    assert not list(tmp_path.glob("report_ERROR.csv"))
    assert not list(tmp_path.glob("report_WARN.csv"))


@pytest.mark.parametrize(
    "source_name, split_by_level, max_export_bytes",
    [
        ("report.csv", False, None),
        ("report_summary.csv", True, None),
        ("report_WARN.csv", True, None),
        ("report_part001.csv", False, 1),
        ("report_manifest.json", False, None),
    ],
    ids=[
        'source-is-report',
        'source-is-summary',
        'source-is-level-part',
        'source-is-size-part',
        'source-is-manifest',
    ],
)
def test_bundle_never_overwrites_any_input_source_even_when_overwrite_is_enabled(
    tmp_path, source_name, split_by_level, max_export_bytes
):
    """情境：報表、摘要、分檔或清單與來源同名時即使授權覆蓋仍應拒絕。"""
    result = _result(tmp_path / "logs", source_names=(source_name,))
    source = Path(result.input_path) / source_name
    original_bytes = source.read_bytes()
    options = ResultExportOptions(
        output_path=str(Path(result.input_path) / "report.csv"),
        split_by_level=split_by_level,
        max_export_bytes=max_export_bytes,
        overwrite=True,
    )
    before = _snapshot_metadata(result)

    with pytest.raises(ValueError, match="來源"):
        export_analysis_bundle(result, options)

    assert source.read_bytes() == original_bytes
    assert _snapshot_metadata(result) == before
    # The source may itself use a generated-looking name. It must remain the
    # only entry in the input directory; no staged/published output is left.
    assert list(Path(result.input_path).iterdir()) == [source]


def test_bundle_overwrite_replaces_old_report_family_and_manifest_after_rendering(tmp_path):
    """情境：授權覆蓋應先完成內容再替換舊報表組與清單。"""
    result = _result(tmp_path / "logs")
    output = tmp_path / "report.csv"
    old_files = [
        tmp_path / "report_summary.csv",
        tmp_path / "report_ERROR.csv",
        tmp_path / "report_WARN.csv",
        tmp_path / "report_manifest.json",
    ]
    for path in old_files:
        path.write_bytes(b"previous output")
    options = ResultExportOptions(
        output_path=str(output), split_by_level=True, overwrite=True
    )

    reports, manifest_path = export_analysis_bundle(result, options)

    assert all(Path(path).read_bytes() != b"previous output" for path in [*reports, manifest_path])
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert all(
        entry["sha256"] == hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for entry, path in zip(manifest["outputs"], reports, strict=True)
    )
    assert not list(tmp_path.glob(".report.csv.export-*"))
