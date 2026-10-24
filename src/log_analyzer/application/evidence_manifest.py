from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..infrastructure.exporting.fields import normalize_export_fields
from ..infrastructure.exporting.transaction import ExportTransaction
from ..version import get_package_version
from .analysis_service import AnalysisResult, ResultExportOptions

_HASH_CHUNK_SIZE = 1024 * 1024
_VERIFICATION_LIMITATION = (
    "Source verification compares scan-time size and nanosecond modification time "
    "with metadata before and after export-time hashing. The SHA256 is computed at "
    "export time; this is not a content snapshot and does not prove the bytes parsed "
    "during analysis."
)


def export_evidence_manifest(
    result: AnalysisResult,
    options: ResultExportOptions,
    report_files: Sequence[str],
) -> str:
    manifest = _build_evidence_manifest(result, options, report_files)
    manifest_path = _manifest_path(options)
    transaction = ExportTransaction(
        manifest_path,
        overwrite=options.overwrite,
        protected_paths=_source_paths(result),
    )
    try:
        staged_path = transaction.stage_path(manifest_path.name)
        _write_manifest_file(staged_path, manifest)
        published = transaction.publish([staged_path])
        return os.path.abspath(published[0])
    finally:
        transaction.cleanup()


def _stage_evidence_manifest(
    result: AnalysisResult,
    options: ResultExportOptions,
    report_files: Sequence[str],
    transaction: ExportTransaction,
) -> str:
    manifest = _build_evidence_manifest(result, options, report_files)
    staged_path = transaction.stage_path(_manifest_path(options).name)
    _write_manifest_file(staged_path, manifest)
    return str(staged_path)


def _manifest_path(options: ResultExportOptions) -> Path:
    output_path = Path(options.output_path)
    return output_path.with_name(f"{output_path.stem}_manifest.json")


def _source_paths(result: AnalysisResult) -> tuple[Path, ...]:
    root = Path(result.input_path)
    sources: list[Path] = []
    if root.is_file():
        sources.append(root)
    for filename in result.source_states:
        candidate = Path(filename)
        sources.append(candidate if candidate.is_absolute() else root / candidate)
    return tuple(sources)


def _build_evidence_manifest(
    result: AnalysisResult,
    options: ResultExportOptions,
    report_files: Sequence[str],
) -> dict:
    """將凍結的分析條件、來源核對結果與報表摘要原子寫成 JSON。"""
    request = result.analysis_request
    if request is None:
        raise ValueError("evidence manifest requires a frozen analysis request")

    outputs = [_describe_report(path) for path in report_files]
    try:
        root = Path(result.input_path).resolve()
    except (OSError, RuntimeError, ValueError):
        root = None
    sources = [
        _describe_source(root, filename, snapshot)
        for filename, snapshot in sorted(result.source_states.items(), key=lambda item: str(item[0]))
    ]

    manifest = {
        "schema_version": 1,
        "tool": {"name": "java-log-analyzer", "version": get_package_version()},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "analysis": {
            "filters": {
                "start_dt": _datetime_string(request.start_dt),
                "end_dt": _datetime_string(request.end_dt),
                "levels": None if request.levels is None else list(request.levels),
                "pattern": request.log_pattern,
                "keyword": request.keyword,
                "ignore_case": request.ignore_case,
                "sort_by": request.sort_by,
            },
            "counts": dict(result.counts),
            "total_logs": result.total_logs,
            "matched_groups": result.matched_groups,
            "matched_occurrences": result.matched_occurrences,
            "diagnostics": asdict(result.diagnostics),
        },
        "export": {
            "fields": list(normalize_export_fields(options.fields)),
            "format": options.fmt.lower(),
            "split_by_level": options.split_by_level,
            "max_export_bytes": options.max_export_bytes,
        },
        "sources": sources,
        "outputs": outputs,
        "limitations": _VERIFICATION_LIMITATION,
    }
    return manifest


def _datetime_string(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _describe_report(filename: str) -> dict[str, str | int]:
    report_path = Path(filename)
    digest = hashlib.sha256()
    size = 0
    with report_path.open("rb") as file_handle:
        while chunk := file_handle.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return {"filename": report_path.name, "size": size, "sha256": digest.hexdigest()}


def _describe_source(root: Path | None, filename: str, snapshot: dict) -> dict:
    display_name = _source_display_name(filename)
    entry = {
        "filename": display_name,
        "scanned": {
            "size": snapshot.get("size"),
            "mtime_ns": snapshot.get("mtime_ns"),
            "stable": snapshot.get("stable"),
        },
        "verification_basis": "size_mtime_ns",
    }
    source_path = _resolve_safe_source_path(root, filename)
    status, sha256_at_export = _verify_source(source_path, snapshot)
    entry["verification_status"] = status
    if sha256_at_export is not None:
        entry["sha256_at_export"] = sha256_at_export
    return entry


def _verify_source(source_path: Path | None, snapshot: dict) -> tuple[str, str | None]:
    if source_path is None:
        return "unsafe", None
    if not snapshot.get("stable"):
        return "unstable", None
    return _verify_stable_source(source_path, snapshot)


def _verify_stable_source(source_path: Path, snapshot: dict) -> tuple[str, str | None]:
    status, before = _source_stat(source_path)
    if status != "ready":
        return status, None
    if not _matches_scan_metadata(before, snapshot):
        return "changed", None

    status, digest = _hash_source(source_path)
    if status != "ready":
        return status, None
    status, after = _source_stat(source_path)
    if status != "ready":
        return status, None
    if not _matches_scan_metadata(after, snapshot):
        return "changed", None
    return "verified", digest


def _source_stat(source_path: Path) -> tuple[str, os.stat_result | None]:
    try:
        return "ready", source_path.stat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None


def _hash_source(source_path: Path) -> tuple[str, str | None]:
    digest = hashlib.sha256()
    try:
        with source_path.open("rb") as file_handle:
            while chunk := file_handle.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    return "ready", digest.hexdigest()


def _resolve_safe_source_path(root: Path | None, filename: str) -> Path | None:
    if root is None:
        return None
    windows_path = PureWindowsPath(filename)
    posix_path = PurePosixPath(filename)
    relative_path = Path(filename)
    if (
        relative_path.is_absolute()
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in relative_path.parts
        or ".." in windows_path.parts
        or ".." in posix_path.parts
    ):
        return None

    try:
        resolved = (root / relative_path).resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def _source_display_name(filename: str) -> str:
    windows_path = PureWindowsPath(filename)
    posix_path = PurePosixPath(filename)
    if (
        Path(filename).is_absolute()
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        return windows_path.name or posix_path.name or "[unsafe source path]"
    return filename.replace("\\", "/")


def _matches_scan_metadata(current, snapshot: dict) -> bool:
    return (
        current.st_size == snapshot.get("size")
        and current.st_mtime_ns == snapshot.get("mtime_ns")
    )


def _write_manifest_file(path: Path, manifest: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file_handle:
        json.dump(manifest, file_handle, ensure_ascii=False, indent=2)
        file_handle.write("\n")
