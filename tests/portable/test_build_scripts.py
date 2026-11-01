from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _create_project(tmp_path: Path) -> Path:
    project = tmp_path / "portable build fixture"
    portable_tools = project / "tools" / "portable"
    portable_tools.mkdir(parents=True)
    shutil.copy2(REPOSITORY_ROOT / "build-portable.cmd", project)
    shutil.copy2(REPOSITORY_ROOT / "build-portable.sh", project)
    (portable_tools / "build.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['BUILD_LOG']).write_text(json.dumps({"
        "'executable': sys.executable, 'arguments': sys.argv}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return project


def _set_path(environment: dict[str, str], value: str) -> None:
    path_key = next((key for key in environment if key.lower() == "path"), "PATH")
    environment[path_key] = value


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def _make_stub_commands(
    tmp_path: Path,
    *,
    include_uv: bool = True,
    uv_exit_code: int = 0,
) -> tuple[Path, Path, Path]:
    command_dir = tmp_path / "stub commands"
    command_dir.mkdir()
    uv_call_log = tmp_path / "uv-call.txt"
    build_log = tmp_path / "build-call.json"

    if os.name == "nt":
        if include_uv:
            (command_dir / "uv.cmd").write_text(
                "@echo off\r\n"
                ">%UV_CALL_LOG% echo %*\r\n"
                ">>%UV_CALL_LOG% echo %UV_PROJECT_ENVIRONMENT%\r\n"
                "if not \"%UV_EXIT_CODE%\"==\"0\" exit /b %UV_EXIT_CODE%\r\n"
                "\"%TEST_PYTHON%\" -m venv \"%UV_PROJECT_ENVIRONMENT%\"\r\n"
                "exit /b %ERRORLEVEL%\r\n",
                encoding="utf-8",
            )
    else:
        for command in ("objdump", "objcopy"):
            _write_executable(command_dir / command, "#!/bin/sh\nexit 0\n")
        _write_executable(command_dir / "uname", "#!/bin/sh\nprintf 'Linux\\n'\n")
        _write_executable(
            command_dir / "dirname",
            "#!/bin/sh\n"
            "[ \"${1-}\" = -- ] && shift\n"
            "case \"$1\" in */*) printf '%s\\n' \"${1%/*}\";; *) printf '.\\n';; esac\n",
        )
        if include_uv:
            _write_executable(
                command_dir / "uv",
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" > \"$UV_CALL_LOG\"\n"
                "printf '%s\\n' \"$UV_PROJECT_ENVIRONMENT\" >> \"$UV_CALL_LOG\"\n"
                "if [ \"${UV_EXIT_CODE:-0}\" -ne 0 ]; then exit \"$UV_EXIT_CODE\"; fi\n"
                "\"$TEST_PYTHON\" -m venv \"$UV_PROJECT_ENVIRONMENT\"\n",
            )

    return command_dir, uv_call_log, build_log


def _run_build_script(project: Path, environment: dict[str, str]):
    if os.name == "nt":
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(project / "build-portable.cmd")]
        return subprocess.run(
            command,
            cwd=project,
            env=environment,
            input="x\r\n",
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
        )
    return subprocess.run(
        ["/bin/sh", str(project / "build-portable.sh")],
        cwd=project,
        env=environment,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )


def _new_environment(
    tmp_path: Path,
    *,
    include_uv: bool = True,
    uv_exit_code: int = 0,
) -> tuple[Path, dict[str, str], Path, Path]:
    project = _create_project(tmp_path)
    command_dir, uv_call_log, build_log = _make_stub_commands(
        tmp_path,
        include_uv=include_uv,
        uv_exit_code=uv_exit_code,
    )
    environment = os.environ.copy()
    command_dir = tmp_path / "stub commands"
    _set_path(environment, str(command_dir))
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    environment["UV_CALL_LOG"] = str(uv_call_log)
    environment["BUILD_LOG"] = str(build_log)
    environment["TEST_PYTHON"] = sys.executable
    environment["UV_EXIT_CODE"] = str(uv_exit_code)
    return project, environment, uv_call_log, build_log


def _assert_successful_build(
    project: Path,
    environment: dict[str, str],
    uv_call_log: Path,
    build_log: Path,
) -> None:
    result = _run_build_script(project, environment)

    assert result.returncode == 0, result.stdout + result.stderr
    log_lines = uv_call_log.read_text(encoding="utf-8").splitlines()
    assert log_lines[0] == "sync --python 3.12 --locked --extra portable --no-editable"
    expected_environment = (project / ".venv-portable").resolve()
    assert Path(log_lines[1]).resolve() == expected_environment

    assert build_log.exists(), result.stdout + result.stderr
    build_call = json.loads(build_log.read_text(encoding="utf-8"))
    expected_python = expected_environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert Path(build_call["executable"]).resolve() == expected_python.resolve()
    assert build_call["arguments"] == ["tools/portable/build.py"]
    assert "Build completed" in result.stdout
    assert "dist/portable" in result.stdout.replace("\\", "/")


@pytest.mark.skipif(os.name == "nt", reason="Linux 建置腳本由 Linux CI 執行")
def test_linux_portable_build_uses_locked_uv_sync_and_target_environment(tmp_path):
    project, environment, uv_call_log, build_log = _new_environment(tmp_path)

    _assert_successful_build(project, environment, uv_call_log, build_log)


@pytest.mark.skipif(os.name != "nt", reason="Windows 建置腳本由 Windows CI 執行")
def test_windows_portable_build_uses_locked_uv_sync_and_target_environment(tmp_path):
    project, environment, uv_call_log, build_log = _new_environment(tmp_path)

    _assert_successful_build(project, environment, uv_call_log, build_log)


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_portable_script_overwrites_external_environment_to_project_local_path(
    tmp_path, platform_name
):
    if platform_name != ("windows" if os.name == "nt" else "linux"):
        pytest.skip(f"{platform_name} 建置腳本由對應平台 CI 執行")
    project = _create_project(tmp_path)
    protected_environment = project / ".venv"
    protected_environment.mkdir()
    marker = protected_environment / "preserve-me.txt"
    marker.write_text("development environment", encoding="utf-8")
    command_dir, uv_call_log, build_log = _make_stub_commands(tmp_path)
    environment = os.environ.copy()
    _set_path(environment, str(command_dir))
    environment["UV_PROJECT_ENVIRONMENT"] = str(protected_environment)
    environment["UV_CALL_LOG"] = str(uv_call_log)
    environment["BUILD_LOG"] = str(build_log)
    environment["TEST_PYTHON"] = sys.executable
    environment["UV_EXIT_CODE"] = "0"

    _assert_successful_build(project, environment, uv_call_log, build_log)
    assert marker.read_text(encoding="utf-8") == "development environment"


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_portable_script_stops_when_uv_is_missing(tmp_path, platform_name):
    if platform_name != ("windows" if os.name == "nt" else "linux"):
        pytest.skip(f"{platform_name} 建置腳本由對應平台 CI 執行")
    project, environment, uv_call_log, build_log = _new_environment(
        tmp_path,
        include_uv=False,
    )

    result = _run_build_script(project, environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "uv" in output.lower()
    assert "install" in output.lower()
    assert not uv_call_log.exists()
    assert not build_log.exists()


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_portable_script_preserves_existing_python_314_environment(tmp_path, platform_name):
    if platform_name != ("windows" if os.name == "nt" else "linux"):
        pytest.skip(f"{platform_name} 建置腳本由對應平台 CI 執行")
    project, environment, uv_call_log, build_log = _new_environment(tmp_path)
    existing_environment = project / ".venv-portable"
    subprocess.run(
        [sys.executable, "-m", "venv", str(existing_environment)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    marker = existing_environment / "preserve-me.txt"
    marker.write_text("existing environment", encoding="utf-8")
    sitecustomize = tmp_path / "incompatible-python"
    sitecustomize.mkdir()
    (sitecustomize / "sitecustomize.py").write_text(
        "import sys\nsys.version_info = (3, 14, 0, 'final', 0)\n",
        encoding="utf-8",
    )
    environment["PYTHONPATH"] = str(sitecustomize)

    result = _run_build_script(project, environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Python 3.12" in output
    assert not uv_call_log.exists()
    assert not build_log.exists()
    assert marker.read_text(encoding="utf-8") == "existing environment"


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_portable_script_rejects_environment_symlink_or_junction(tmp_path, platform_name):
    if platform_name != ("windows" if os.name == "nt" else "linux"):
        pytest.skip(f"{platform_name} 建置腳本由對應平台 CI 執行")
    project, environment, uv_call_log, build_log = _new_environment(tmp_path)
    protected_environment = project / ".venv"
    protected_environment.mkdir()
    marker = protected_environment / "preserve-me.txt"
    marker.write_text("development environment", encoding="utf-8")
    portable_environment = project / ".venv-portable"
    if os.name == "nt":
        result = subprocess.run(
            [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "mklink",
                "/J",
                str(portable_environment),
                str(protected_environment),
            ],
            encoding="utf-8",
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("此 Windows 執行環境不允許建立目錄 junction")
    else:
        portable_environment.symlink_to(protected_environment, target_is_directory=True)

    result = _run_build_script(project, environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "symlink" in output.lower() or "junction" in output.lower()
    assert not uv_call_log.exists()
    assert not build_log.exists()
    assert marker.read_text(encoding="utf-8") == "development environment"


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_portable_script_stops_after_uv_sync_failure(tmp_path, platform_name):
    if platform_name != ("windows" if os.name == "nt" else "linux"):
        pytest.skip(f"{platform_name} 建置腳本由對應平台 CI 執行")
    project, environment, uv_call_log, build_log = _new_environment(
        tmp_path,
        uv_exit_code=37,
    )
    if os.name == "nt":
        environment["BUILD_RESULT"] = "23"

    result = _run_build_script(project, environment)

    if os.name == "nt":
        assert result.returncode == 37
    else:
        assert result.returncode != 0
    assert uv_call_log.exists()
    assert not build_log.exists()
