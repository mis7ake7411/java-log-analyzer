from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path


_PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def get_package_version() -> str:
    """先讀取專案版本，失敗時回退到已安裝套件版本"""
    source_version = _get_source_version()
    if source_version:
        return source_version

    try:
        return package_version("java-log-analyzer")
    except PackageNotFoundError:
        return "unknown"


def _get_source_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        content = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return ""

    match = _PYPROJECT_VERSION_RE.search(content)
    if not match:
        return ""
    return match.group(1).strip()
