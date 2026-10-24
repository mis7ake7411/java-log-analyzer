"""Build a platform-native, one-folder portable archive."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORTABLE_DIR = PROJECT_ROOT / "tools" / "portable"
SPEC_FILE = PORTABLE_DIR / "app.spec"
PYINSTALLER_VERSION = "6.22.3"
APPLICATION_NAME = "log-analyzer"


class BuildError(Exception):
    """A user-facing build precondition or packaging error."""


def read_project_version() -> str:
    """Read the static [project] version without requiring tomllib."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    project_section = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", content)
    if project_section is None:
        raise BuildError(f"找不到 {pyproject} 的 [project] 區段。")

    version_match = re.search(
        r"(?m)^\s*version\s*=\s*(['\"])([^'\"]+)\1\s*(?:#.*)?$",
        project_section.group(1),
    )
    if version_match is None:
        raise BuildError("pyproject.toml 的 [project] 需要明確指定 version。")
    return version_match.group(2)


def normalize_platform() -> tuple[str, str, str]:
    """Return the archive platform, architecture, and extension."""
    system = platform.system()
    if system == "Windows":
        platform_name, extension = "windows", ".zip"
    elif system == "Linux":
        platform_name, extension = "linux", ".tar.gz"
    else:
        raise BuildError(f"目前只支援 Windows 與 Linux 原生建置；偵測到 {system}。")

    machine = platform.machine().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
    }
    architecture = aliases.get(machine, re.sub(r"[^a-z0-9_.-]+", "-", machine))
    if not architecture:
        raise BuildError("無法辨識目前建置環境的 CPU 架構。")
    return platform_name, architecture, extension


def verify_build_environment(project_version: str) -> None:
    """Reject stale project metadata and missing or mismatched PyInstaller."""
    try:
        installed_version = importlib.metadata.version("java-log-analyzer")
    except importlib.metadata.PackageNotFoundError as exc:
        raise BuildError(
            "目前 Python 環境沒有安裝 java-log-analyzer；請在隔離建置環境安裝目前專案後再執行。"
        ) from exc
    if installed_version != project_version:
        raise BuildError(
            "安裝環境的 java-log-analyzer 版本為 "
            f"{installed_version}，但 pyproject.toml 為 {project_version}；拒絕使用過期 metadata 打包。"
        )

    try:
        installed_pyinstaller = importlib.metadata.version("PyInstaller")
    except importlib.metadata.PackageNotFoundError as exc:
        raise BuildError(
            f"此環境未安裝 PyInstaller=={PYINSTALLER_VERSION}；請先準備建置環境，工具不會自行安裝套件。"
        ) from exc
    if installed_pyinstaller != PYINSTALLER_VERSION:
        raise BuildError(
            f"此環境的 PyInstaller 版本為 {installed_pyinstaller}，"
            f"預期為 {PYINSTALLER_VERSION}。"
        )


def validate_bundle(bundle_dir: Path, platform_name: str) -> None:
    """Confirm PyInstaller emitted the expected onedir structure."""
    executable = bundle_dir / (f"{APPLICATION_NAME}.exe" if platform_name == "windows" else APPLICATION_NAME)
    if not bundle_dir.is_dir():
        raise BuildError(f"PyInstaller 沒有產生預期的目錄：{bundle_dir}")
    if not executable.is_file():
        raise BuildError(f"打包目錄缺少執行檔：{executable}")
    if not (bundle_dir / "_internal").is_dir():
        raise BuildError(f"打包目錄缺少 PyInstaller 執行資源目錄：{bundle_dir / '_internal'}")


def add_start_here(bundle_dir: Path, platform_name: str) -> None:
    if platform_name == "windows":
        launch = "Windows：雙擊 log-analyzer.exe 啟動；也可在終端機執行 log-analyzer.exe。"
    else:
        launch = "Linux：在此資料夾開啟終端機，執行 ./log-analyzer。"
    text = (
        "Java Log Analyzer 可攜版\n\n"
        f"{launch}\n"
        "具名配方會存放在目前使用者的設定目錄。\n"
    )
    shutil.copy2(PROJECT_ROOT / "LICENSE", bundle_dir / "LICENSE")
    (bundle_dir / "START-HERE.txt").write_text(text, encoding="utf-8")


def archive_members(bundle_dir: Path):
    yield bundle_dir
    yield from sorted(bundle_dir.rglob("*"), key=lambda path: path.relative_to(bundle_dir).as_posix())


def make_zip(bundle_dir: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in archive_members(bundle_dir):
            relative = path.relative_to(bundle_dir)
            archive_name = APPLICATION_NAME
            if relative.parts:
                archive_name = str(PurePosixPath(APPLICATION_NAME, relative.as_posix()))
            if path.is_dir() and not path.is_symlink():
                archive_name += "/"
            archive.write(path, archive_name)


def make_tar_gz(bundle_dir: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, mode="w:gz", format=tarfile.PAX_FORMAT, dereference=False) as archive:
        for path in archive_members(bundle_dir):
            relative = path.relative_to(bundle_dir)
            archive_name = APPLICATION_NAME
            if relative.parts:
                archive_name = PurePosixPath(APPLICATION_NAME, relative.as_posix()).as_posix()
            archive.add(path, arcname=archive_name, recursive=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(output_dir: Path) -> tuple[Path, Path, str]:
    project_version = read_project_version()
    verify_build_environment(project_version)
    platform_name, architecture, extension = normalize_platform()

    archive_name = (
        f"{APPLICATION_NAME}-{project_version}-{platform_name}-{architecture}{extension}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.resolve()
    archive_path = output_dir / archive_name
    checksum_path = output_dir / f"{archive_name}.sha256"

    # Keep scratch output on the destination volume for atomic replacement of the archive file.
    with tempfile.TemporaryDirectory(prefix=".log-analyzer-build-", dir=output_dir) as temp_name:
        scratch = Path(temp_name)
        dist_dir = scratch / "dist"
        work_dir = scratch / "work"
        bundle_dir = dist_dir / APPLICATION_NAME
        env = os.environ.copy()
        env["PYINSTALLER_CONFIG_DIR"] = str(scratch / "pyinstaller-config")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(work_dir),
            str(SPEC_FILE),
        ]
        result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=False)
        if result.returncode != 0:
            raise BuildError(f"PyInstaller 建置失敗，結束代碼：{result.returncode}。")

        validate_bundle(bundle_dir, platform_name)
        add_start_here(bundle_dir, platform_name)

        staged_archive = scratch / archive_name
        if platform_name == "windows":
            make_zip(bundle_dir, staged_archive)
        else:
            make_tar_gz(bundle_dir, staged_archive)
        digest = sha256_file(staged_archive)
        staged_checksum = scratch / f"{archive_name}.sha256"
        staged_checksum.write_text(f"{digest}  {archive_name}\n", encoding="ascii")

        # The archive is replaced atomically; its checksum sidecar is replaced separately.
        os.replace(staged_archive, archive_path)
        os.replace(staged_checksum, checksum_path)

    return archive_path, checksum_path, digest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="以目前原生 Windows/Linux 環境建置 onedir 可攜壓縮包與 SHA-256。"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist" / "portable",
        help="成品輸出目錄（預設：專案 dist/portable）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        archive_path, checksum_path, digest = build(args.output.expanduser())
    except (BuildError, OSError) as exc:
        print(f"可攜版建置失敗：{exc}", file=sys.stderr)
        return 1

    print(f"壓縮包：{archive_path}")
    print(f"SHA-256：{digest}")
    print(f"校驗檔：{checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
