"""Collect pytest files and run one deterministic file-level shard."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Sequence
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
SHARD_COUNT = 2

for import_root in (REPOSITORY_ROOT, SOURCE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


class CollectedNodeIds:
    """Capture node IDs from pytest's collection hook."""

    def __init__(self) -> None:
        self.nodeids: list[str] = []

    def pytest_collection_finish(self, session: Any) -> None:
        self.nodeids = [item.nodeid for item in session.items]


def normalize_test_file(path_or_nodeid: str) -> str:
    path = path_or_nodeid.split("::", maxsplit=1)[0].replace("\\", "/")
    return PurePosixPath(path.removeprefix("./")).as_posix()


def count_collected_files(nodeids: Iterable[str]) -> dict[str, int]:
    counts = Counter(normalize_test_file(nodeid) for nodeid in nodeids)
    return dict(sorted(counts.items()))


def partition_test_files(
    files: Iterable[str], shard_count: int = SHARD_COUNT
) -> tuple[tuple[str, ...], ...]:
    if shard_count < 2:
        raise ValueError("shard_count must be at least 2")

    ordered_files = sorted({normalize_test_file(path) for path in files})
    if len(ordered_files) < shard_count:
        raise ValueError("each shard needs at least one collected test file")

    return tuple(
        tuple(ordered_files[index::shard_count]) for index in range(shard_count)
    )


def validate_partition(
    expected_files: Iterable[str], shards: Sequence[Sequence[str]]
) -> None:
    expected = {normalize_test_file(path) for path in expected_files}
    assigned = [normalize_test_file(path) for shard in shards for path in shard]

    if any(not shard for shard in shards):
        raise ValueError("every shard must contain at least one test file")
    if len(assigned) != len(set(assigned)):
        raise ValueError("a test file was assigned to more than one shard")
    if set(assigned) != expected:
        raise ValueError("shards must cover every collected test file exactly once")


def collect_test_files() -> tuple[dict[str, int], int]:
    import pytest

    collector = CollectedNodeIds()
    original_directory = Path.cwd()
    try:
        os.chdir(REPOSITORY_ROOT)
        exit_code = int(
            pytest.main(["--collect-only", "-qq"], plugins=[collector])
        )
    finally:
        os.chdir(original_directory)

    return count_collected_files(collector.nodeids), exit_code


def execute_pytest(files: Sequence[str], junitxml: Path) -> int:
    environment = os.environ.copy()
    current_pythonpath = environment.get("PYTHONPATH")
    source_path = str(SOURCE_ROOT)
    if current_pythonpath:
        pythonpath_entries = current_pythonpath.split(os.pathsep)
        if source_path not in pythonpath_entries:
            environment["PYTHONPATH"] = os.pathsep.join(
                [source_path, current_pythonpath]
            )
    else:
        environment["PYTHONPATH"] = source_path

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *files,
        "--durations=20",
        f"--junitxml={junitxml}",
    ]
    return subprocess.run(
        command, cwd=REPOSITORY_ROOT, env=environment
    ).returncode


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one pytest file-level shard.")
    parser.add_argument(
        "--shard",
        type=int,
        choices=range(1, SHARD_COUNT + 1),
        required=True,
        help=f"shard number from 1 to {SHARD_COUNT}",
    )
    parser.add_argument(
        "--junitxml",
        type=Path,
        help="JUnit XML output path (default: test-results/shard-N.xml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="collect and print the shard file list without running tests",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    files_by_case_count, collection_exit_code = collect_test_files()
    if collection_exit_code:
        return collection_exit_code
    if not files_by_case_count:
        print("pytest collection found no tests", file=sys.stderr)
        return 1

    shard_files = partition_test_files(files_by_case_count, SHARD_COUNT)
    validate_partition(files_by_case_count, shard_files)

    total_cases = sum(files_by_case_count.values())
    selected_files = shard_files[args.shard - 1]
    selected_cases = sum(files_by_case_count[path] for path in selected_files)
    print(
        f"Collected {total_cases} test cases in {len(files_by_case_count)} files; "
        f"shard {args.shard}/{SHARD_COUNT}: "
        f"{selected_cases} cases in {len(selected_files)} files."
    )
    if args.dry_run:
        for path in selected_files:
            print(path)
        return 0

    junitxml = args.junitxml or Path("test-results") / f"shard-{args.shard}.xml"
    if not junitxml.is_absolute():
        junitxml = REPOSITORY_ROOT / junitxml
    junitxml.parent.mkdir(parents=True, exist_ok=True)
    return execute_pytest(selected_files, junitxml)


if __name__ == "__main__":
    raise SystemExit(main())
