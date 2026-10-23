import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _cli_environment():
    environment = os.environ.copy()
    source_path = str(REPOSITORY_ROOT / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_path, existing_pythonpath) if value
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _run_cli(source, output_path, *options):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "log_analyzer",
            str(source),
            "-o",
            str(output_path),
            *options,
        ],
        cwd=output_path.parent,
        env=_cli_environment(),
        encoding="utf-8",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _write_cli_log(source, message):
    source.write_text(
        f"2026-10-22 12:00:00.000 [main] ERROR com.test.App - {message}\n",
        encoding="utf-8",
    )


def test_cli_single_file_xml_sampling_and_json_export_exclude_siblings(tmp_path):
    selected_file = tmp_path / "chosen.log"
    sibling_file = tmp_path / "sibling.txt"
    xml_file = tmp_path / "logback.xml"
    output_file = tmp_path / "report.json"
    selected_file.write_text(
        "2026-05-25 15:50:33,198 INFO  [main] YtMamApp: selected-only-marker\n",
        encoding="utf-8",
    )
    sibling_file.write_text(
        "[2026-06-06 10:00:00.001] [INFO ] [main] com.test.App.run(12) - sibling-only-marker\n"
        "[2026-06-06 10:00:00.002] [INFO ] [main] com.test.App.run(13) - sibling-only-marker\n"
        "[2026-06-06 10:00:00.003] [INFO ] [main] com.test.App.run(14) - sibling-only-marker\n",
        encoding="utf-8",
    )
    xml_file.write_text(
        """<configuration>
    <property name="CONSOLE_LOG_PATTERN" value="[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n"/>
    <property name="FILE_LOG_PATTERN" value="%d %-5level [%thread] %logger{0}: %msg%n"/>
</configuration>
""",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    source_path = str(REPOSITORY_ROOT / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_path, existing_pythonpath) if value
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "log_analyzer",
            str(selected_file),
            "--logback-xml",
            str(xml_file),
            "-f",
            "json",
            "-o",
            str(output_file),
        ],
        cwd=tmp_path,
        env=environment,
        encoding="utf-8",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "來源模式：單一檔案" in completed.stdout
    assert "來源檔數：1" in completed.stdout
    assert "  - chosen.log" in completed.stdout
    assert "FILE_LOG_PATTERN，命中 1/1" in completed.stdout
    assert completed.stdout.index("來源模式：單一檔案") < completed.stdout.index(
        "已從 logback XML 選用 pattern"
    )
    exported_text = output_file.read_text(encoding="utf-8")
    json.loads(exported_text)
    assert "selected-only-marker" in exported_text
    assert "sibling-only-marker" not in exported_text


def test_cli_default_export_conflict_preserves_existing_report(tmp_path):
    source = tmp_path / "application.log"
    output = tmp_path / "report.json"
    _write_cli_log(source, "original report contents")

    initial = _run_cli(source, output, "-f", "json")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    original_bytes = output.read_bytes()

    _write_cli_log(source, "changed source contents")
    retry = _run_cli(source, output, "-f", "json")

    assert retry.returncode != 0
    assert str(output.resolve()) in retry.stdout
    assert "--overwrite" in retry.stdout
    assert "Traceback" not in retry.stderr
    assert output.read_bytes() == original_bytes


def test_cli_overwrite_replaces_existing_report_when_explicitly_requested(tmp_path):
    source = tmp_path / "application.log"
    output = tmp_path / "report.json"
    _write_cli_log(source, "original report contents")

    initial = _run_cli(source, output, "-f", "json")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    original_bytes = output.read_bytes()
    _write_cli_log(source, "replacement report contents")

    overwritten = _run_cli(source, output, "-f", "json", "--overwrite")

    assert overwritten.returncode == 0, overwritten.stdout + overwritten.stderr
    assert output.read_bytes() != original_bytes
    assert "replacement report contents" in output.read_text(encoding="utf-8")


def test_cli_summary_conflict_reports_path_without_creating_level_reports(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "error.log").write_text(
        "2026-10-22 12:00:00.000 [main] ERROR com.test.App - error marker\n",
        encoding="utf-8",
    )
    (logs / "warn.log").write_text(
        "2026-10-22 12:00:01.000 [main] WARN com.test.App - warning marker\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.csv"
    summary = tmp_path / "report_summary.csv"
    summary.write_bytes(b"keep this summary\n")

    completed = _run_cli(logs, output, "--split-by-level")

    assert completed.returncode != 0
    assert str(summary.resolve()) in completed.stdout
    assert "--overwrite" in completed.stdout
    assert summary.read_bytes() == b"keep this summary\n"
    assert sorted(path.name for path in tmp_path.glob("report_*.csv")) == [
        "report_summary.csv"
    ]


def test_cli_late_part_conflict_preserves_output_family(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    source = logs / "large.log"
    with source.open("w", encoding="utf-8") as log_file:
        for index in range(2500):
            log_file.write(
                f"2026-10-22 12:00:00.000 [main] ERROR com.test.App - "
                f"{index:04d} {'x' * 1000}\n"
            )
    output_dir = tmp_path / "exports"
    output_dir.mkdir()
    output = output_dir / "split.csv"

    initial = _run_cli(logs, output, "--max-export-mb", "1")
    assert initial.returncode == 0, initial.stdout + initial.stderr
    output_family = sorted(output_dir.glob("split*"))
    parts = sorted(output_dir.glob("split_part*.csv"))
    assert len(parts) >= 2
    late_part = parts[-1]
    assert late_part.name != "split_summary.csv"
    for earlier_output in output_family:
        if earlier_output != late_part:
            earlier_output.unlink()
    preserved_bytes = late_part.read_bytes()

    retry = _run_cli(logs, output, "--max-export-mb", "1")

    assert retry.returncode != 0
    assert str(late_part.resolve()) in retry.stdout
    assert "--overwrite" in retry.stdout
    assert sorted(output_dir.glob("split*")) == [late_part]
    assert late_part.read_bytes() == preserved_bytes
