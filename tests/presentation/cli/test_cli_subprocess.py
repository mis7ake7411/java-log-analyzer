import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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
