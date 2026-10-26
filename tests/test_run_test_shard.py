from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

try:
    from scripts import run_test_shard
except ModuleNotFoundError:
    run_test_shard = None


def test_partition_covers_each_collected_file_once_and_includes_new_files() -> None:
    assert run_test_shard is not None, "the CI test shard runner is missing"

    nodeids = [
        "tests/presentation/tui/test_tui_future.py::test_new_case",
        r"tests\application\test_source.py::test_existing_case[windows]",
        "tests/test_new_feature.py::test_new_case",
        "tests/application/test_analysis.py::test_first_case",
        "tests/presentation/tui/test_tui.py::test_existing_case",
        "tests/application/test_analysis.py::test_second_case",
    ]
    files = run_test_shard.count_collected_files(nodeids)

    shards = run_test_shard.partition_test_files(files, shard_count=2)

    run_test_shard.validate_partition(files, shards)
    assert files["tests/application/test_analysis.py"] == 2
    assert shards == (
        (
            "tests/application/test_analysis.py",
            "tests/presentation/tui/test_tui.py",
            "tests/test_new_feature.py",
        ),
        (
            "tests/application/test_source.py",
            "tests/presentation/tui/test_tui_future.py",
        ),
    )
    assert run_test_shard.partition_test_files(reversed(files), shard_count=2) == shards


def test_runner_preserves_pytest_failure_exit_code() -> None:
    assert run_test_shard is not None, "the CI test shard runner is missing"
    failure = subprocess.CompletedProcess(args=[], returncode=1)

    with patch.object(run_test_shard.subprocess, "run", return_value=failure) as run:
        exit_code = run_test_shard.execute_pytest(
            ["tests/test_example.py"], Path("test-results/shard-1.xml")
        )

    assert exit_code == 1
    command = run.call_args.args[0]
    assert "tests/test_example.py" in command
    assert "--durations=20" in command
    assert any(argument.startswith("--junitxml=") for argument in command)


def test_runner_rejects_an_invalid_shard_number() -> None:
    assert run_test_shard is not None, "the CI test shard runner is missing"
    runner_path = Path(run_test_shard.__file__)

    result = subprocess.run(
        [sys.executable, str(runner_path), "--shard", "3"],
        cwd=runner_path.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
