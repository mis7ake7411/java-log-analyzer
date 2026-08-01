from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


def normalize_pasted_path(path: str) -> str | None:
    """將終端貼上的單一路徑正規化為絕對路徑。"""
    if "\r" in path or "\n" in path:
        return None

    cleaned = path.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()

    if cleaned.lower().startswith("file:"):
        parsed = urlparse(cleaned)
        if parsed.scheme.lower() != "file":
            return None
        cleaned = unquote(parsed.path)
        if parsed.netloc:
            cleaned = f"//{parsed.netloc}{cleaned}"

    if not cleaned:
        return None
    return os.path.abspath(os.path.expanduser(cleaned))


def validate_pasted_path(path: str, field_id: str) -> tuple[bool, str, str]:
    """依 TUI 欄位規則驗證貼上的路徑，並回傳正規化結果與狀態訊息。"""
    normalized_path = normalize_pasted_path(path)
    if normalized_path is None:
        return False, "", "貼上的路徑不可為空白或包含多行"

    if field_id == "path":
        return _validate_pasted_log_path(normalized_path)
    if field_id == "output_path":
        return _validate_pasted_output_path(normalized_path)
    if field_id == "logback_xml_path":
        return _validate_pasted_logback_xml_path(normalized_path)
    return False, normalized_path, "不支援此欄位的路徑貼上"


def _validate_pasted_log_path(path: str) -> tuple[bool, str, str]:
    if os.path.isfile(path):
        if Path(path).suffix.lower() not in {".log", ".txt", ".out"}:
            return False, path, "Log 檔案僅支援 .log、.txt 或 .out"
        path = os.path.dirname(path)
    _, message, is_valid, _ = inspect_directory_path(path)
    return is_valid, path, message


def _validate_pasted_output_path(path: str) -> tuple[bool, str, str]:
    _, message, is_valid, _ = inspect_directory_path(path, require_writable=True)
    return is_valid, path, message


def _validate_pasted_logback_xml_path(path: str) -> tuple[bool, str, str]:
    if Path(path).suffix.lower() != ".xml":
        return False, path, "Logback XML 僅支援 .xml 檔案"
    _, message, is_valid, _ = inspect_file_path(path)
    return is_valid, path, message


def inspect_directory_path(path: str, require_writable: bool = False) -> tuple[str, str, bool, str]:
    """檢查資料夾路徑是否存在、可讀，必要時也要可寫"""
    cleaned = path.strip()
    if not cleaned:
        return "yellow", "請輸入資料夾路徑", False, ""

    abspath = os.path.abspath(os.path.expanduser(cleaned))
    if not os.path.exists(abspath):
        return "red", f"路徑不存在：{abspath}", False, abspath
    if not os.path.isdir(abspath):
        return "red", f"不是資料夾：{abspath}", False, abspath
    required_mode = os.R_OK | os.X_OK
    if require_writable:
        required_mode |= os.W_OK
    if not os.access(abspath, required_mode):
        if require_writable:
            return "red", f"權限不足，無法寫入：{abspath}", False, abspath
        return "red", f"權限不足，無法讀取：{abspath}", False, abspath
    label = "目標資料夾" if not require_writable else "輸出資料夾"
    return "green", f"{label}：{abspath}", True, abspath


def inspect_file_path(path: str) -> tuple[str, str, bool, str]:
    """檢查檔案路徑是否存在且可讀"""
    cleaned = path.strip()
    if not cleaned:
        return "yellow", "請輸入檔案路徑", False, ""

    abspath = os.path.abspath(os.path.expanduser(cleaned))
    if not os.path.exists(abspath):
        return "red", f"檔案不存在：{abspath}", False, abspath
    if not os.path.isfile(abspath):
        return "red", f"不是檔案：{abspath}", False, abspath
    if not os.access(abspath, os.R_OK):
        return "red", f"權限不足，無法讀取：{abspath}", False, abspath
    return "green", f"檔案：{abspath}", True, abspath


def ensure_readable_directory(path: str) -> str:
    """確認分析來源資料夾可讀，並回傳正規化後路徑"""
    color, message, is_valid, abspath = inspect_directory_path(path)
    if is_valid:
        return abspath
    if color == "red" and "權限不足" in message:
        raise PermissionError(message)
    raise FileNotFoundError(message)


def ensure_writable_directory(path: str) -> str:
    """確認輸出資料夾可寫，並回傳正規化後路徑"""
    color, message, is_valid, abspath = inspect_directory_path(path, require_writable=True)
    if is_valid:
        return abspath
    if color == "red" and "權限不足" in message:
        raise PermissionError(message)
    raise FileNotFoundError(message)


def get_system_root_path() -> Path:
    """回傳目前作業系統的根目錄"""
    return Path(Path.cwd().anchor or os.sep)
