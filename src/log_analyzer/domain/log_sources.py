from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

SUPPORTED_LOG_EXTENSIONS = (".log", ".txt", ".out")


@dataclass(frozen=True, slots=True)
class LogSourceSelection:
    path: str
    root: str
    filenames: tuple[str, ...]
    is_file: bool


def select_log_source(
    path: str,
    *,
    selected_files: tuple[str, ...] | None = None,
) -> LogSourceSelection:
    """正規化日誌來源，並將本次要讀取的檔名凍結成不可變清單。"""
    if not isinstance(path, (str, os.PathLike)) or not os.fspath(path).strip():
        raise ValueError("日誌來源路徑不可為空")

    normalized_path = os.path.abspath(os.path.expanduser(os.fspath(path)))
    selected_names = _normalize_selected_files(selected_files)

    if os.path.isdir(normalized_path):
        if not os.access(normalized_path, os.R_OK | os.X_OK):
            raise PermissionError(f"權限不足，無法讀取日誌資料夾：{normalized_path}")
        filenames = (
            _discover_log_files(normalized_path)
            if selected_names is None
            else _validate_selected_files(normalized_path, selected_names)
        )
        return LogSourceSelection(
            path=normalized_path,
            root=normalized_path,
            filenames=filenames,
            is_file=False,
        )

    if os.path.isfile(normalized_path):
        filename = os.path.basename(normalized_path)
        if not _is_supported_log(filename):
            raise ValueError(
                f"不支援的日誌檔案：{filename}；僅支援 .log、.txt 或 .out"
            )
        if not os.access(normalized_path, os.R_OK):
            raise PermissionError(f"權限不足，無法讀取日誌檔案：{normalized_path}")
        if selected_names is None:
            filenames = (filename,)
        elif not selected_names:
            filenames = ()
        elif selected_names == (filename,):
            filenames = selected_names
        else:
            raise ValueError("單一檔案來源只能選取該檔案本身")
        return LogSourceSelection(
            path=normalized_path,
            root=os.path.dirname(normalized_path),
            filenames=filenames,
            is_file=True,
        )

    raise FileNotFoundError(f"找不到日誌來源：{normalized_path}")


def _normalize_selected_files(
    selected_files: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if selected_files is None:
        return None
    if isinstance(selected_files, str):
        raise ValueError("selected_files 必須是檔名 tuple")
    try:
        filenames = tuple(selected_files)
    except TypeError as error:
        raise ValueError("selected_files 必須是檔名 tuple") from error
    for filename in filenames:
        if not isinstance(filename, str) or not _is_basename(filename):
            raise ValueError(f"選取的日誌檔名必須是單一檔名：{filename!r}")
        if not _is_supported_log(filename):
            raise ValueError(f"不支援的日誌檔案：{filename}；僅支援 .log、.txt 或 .out")
    return filenames


def _validate_selected_files(root: str, filenames: tuple[str, ...]) -> tuple[str, ...]:
    for filename in filenames:
        filepath = os.path.join(root, filename)
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"找不到選取的日誌檔案：{filename}")
        if not os.access(filepath, os.R_OK):
            raise PermissionError(f"權限不足，無法讀取選取的日誌檔案：{filename}")
    return filenames


def _discover_log_files(root: str) -> tuple[str, ...]:
    with os.scandir(root) as entries:
        return tuple(
            sorted(
                entry.name
                for entry in entries
                if entry.is_file(follow_symlinks=False) and _is_supported_log(entry.name)
            )
        )


def _is_basename(filename: str) -> bool:
    return (
        bool(filename)
        and filename not in {".", ".."}
        and not os.path.isabs(filename)
        and not PureWindowsPath(filename).drive
        and os.path.basename(filename) == filename
        and "/" not in filename
        and "\\" not in filename
    )


def _is_supported_log(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_LOG_EXTENSIONS
