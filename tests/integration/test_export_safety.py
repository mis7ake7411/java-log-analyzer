"""Exercise the complete retained-analysis -> report family -> manifest boundary."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from log_analyzer.application.analysis.service import (
    AnalysisRequest,
    ResultExportOptions,
    analyze_logs,
    export_analysis_bundle,
)


@pytest.mark.parametrize("fmt", ["csv", "json", "md"], ids=['format-csv', 'format-json', 'format-md'])
@pytest.mark.parametrize("split", ["none", "size", "level"], ids=['split-none', 'split-size', 'split-level'])
def test_complete_export_family_conflict_then_explicit_replacement(tmp_path, fmt, split):
    """情境：各格式與分檔模式遇衝突應保留整組舊檔而明確覆蓋可成功替換。"""
    source = tmp_path / "input.log"
    source.write_text(
        "2026-10-22 12:00:00.000 [main] ERROR example.App - first error\n"
        "2026-10-22 12:01:00.000 [main] WARN example.App - second warning\n",
        encoding="utf-8",
    )
    original_source = source.read_bytes()
    reference_dir = tmp_path / "reference"
    target_dir = tmp_path / "target"
    reference_dir.mkdir()
    target_dir.mkdir()
    result = analyze_logs(AnalysisRequest(path=str(source)))
    options = ResultExportOptions(
        output_path=str(reference_dir / f"report.{fmt}"),
        fmt=fmt,
        split_by_level=split == "level",
        max_export_bytes=1 if split == "size" else None,
    )
    try:
        reference_reports, reference_manifest = export_analysis_bundle(result, options)
        names = [Path(path).name for path in [*reference_reports, reference_manifest]]
        for name in names:
            (target_dir / name).write_bytes(b"previous user report")
        unrelated = target_dir / f"report_part999.{fmt}"
        unrelated.write_bytes(b"unrelated old part")
        before = {path.name: path.read_bytes() for path in target_dir.iterdir()}
        previous_metadata = (result.output_path, result.format_name, list(result.exported_files))
        target_options = replace(options, output_path=str(target_dir / f"report.{fmt}"))

        with pytest.raises(FileExistsError):
            export_analysis_bundle(result, target_options)

        assert {path.name: path.read_bytes() for path in target_dir.iterdir()} == before
        assert (result.output_path, result.format_name, result.exported_files) == previous_metadata

        reports, manifest_path = export_analysis_bundle(
            result, replace(target_options, overwrite=True)
        )
        assert {path.name for path in target_dir.iterdir()} == set(before)
        assert unrelated.read_bytes() == b"unrelated old part"
        assert source.read_bytes() == original_source
        assert result.exported_files == reports
        assert all(Path(path).parent == target_dir for path in reports)
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert {item["filename"] for item in manifest["outputs"]} == {
            Path(path).name for path in reports
        }
        for item in manifest["outputs"]:
            data = (target_dir / item["filename"]).read_bytes()
            assert item["size"] == len(data)
            assert item["sha256"] == hashlib.sha256(data).hexdigest()
        if split != "none":
            summary = Path(reports[0]).read_text(encoding="utf-8-sig")
            for report in reports[1:]:
                expected = json.dumps(report)[1:-1] if fmt == "json" else report
                assert expected in summary
    finally:
        result.matched_logs.release()
