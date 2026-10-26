from dataclasses import FrozenInstanceError
import pytest


def _source_selection_api():
    try:
        import log_analyzer.domain.log_sources as log_sources
        from log_analyzer.domain.log_sources import (
            SUPPORTED_LOG_EXTENSIONS,
            select_log_source,
        )
    except ModuleNotFoundError:
        pytest.fail("shared log source selection API should be available")
    return SUPPORTED_LOG_EXTENSIONS, select_log_source, log_sources


def test_directory_selection_finds_supported_regular_files_at_current_level(tmp_path):
    """情境：資料夾來源只應列出目前層級支援的一般檔案。"""
    supported_extensions, select_log_source, _log_sources = _source_selection_api()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for filename in ("a.log", "B.LOG", "c.txt", "d.TXT", "e.out", "f.OUT", "ignored.json"):
        (log_dir / filename).touch()
    (log_dir / "folder.log").mkdir()
    nested = log_dir / "nested"
    nested.mkdir()
    (nested / "inside.log").touch()

    source = select_log_source(str(log_dir))

    assert supported_extensions == (".log", ".txt", ".out")
    assert source.path == str(log_dir.resolve())
    assert source.root == str(log_dir.resolve())
    assert source.filenames == ("B.LOG", "a.log", "c.txt", "d.TXT", "e.out", "f.OUT")
    assert source.is_file is False
    with pytest.raises(FrozenInstanceError):
        source.filenames = ()


def test_single_file_selection_keeps_file_path_and_excludes_siblings(tmp_path):
    """情境：指定單檔應保留檔案路徑並排除同層其他日誌。"""
    _supported_extensions, select_log_source, _log_sources = _source_selection_api()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    selected_file = log_dir / "chosen.out"
    selected_file.touch()
    (log_dir / "sibling.log").touch()

    source = select_log_source(str(selected_file))

    assert source.path == str(selected_file.resolve())
    assert source.root == str(log_dir.resolve())
    assert source.filenames == ("chosen.out",)
    assert source.is_file is True


def test_single_file_selection_does_not_enumerate_parent_entries(tmp_path, monkeypatch):
    """情境：單檔來源不應列舉父資料夾的內容。"""
    _supported_extensions, select_log_source, log_sources = _source_selection_api()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    selected_file = log_dir / "chosen.log"
    selected_file.touch()

    def reject_enumeration(*_args, **_kwargs):
        pytest.fail("single-file selection must not enumerate siblings")

    monkeypatch.setattr(log_sources.os, "scandir", reject_enumeration)
    monkeypatch.setattr(log_sources.os, "listdir", reject_enumeration)

    source = select_log_source(str(selected_file))

    assert source.filenames == ("chosen.log",)


def test_explicit_selection_stays_frozen_and_empty_selection_stays_empty(tmp_path):
    """情境：明確選擇應固定且空選擇不得擴大成全部檔案。"""
    _supported_extensions, select_log_source, _log_sources = _source_selection_api()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "chosen.log").touch()
    (log_dir / "other.txt").touch()

    source = select_log_source(str(log_dir), selected_files=("chosen.log",))
    (log_dir / "arrived-later.out").touch()
    empty = select_log_source(str(log_dir), selected_files=())

    assert source.filenames == ("chosen.log",)
    assert empty.filenames == ()


def test_selection_rejects_missing_unsupported_and_escaping_sources(tmp_path):
    """情境：遺失、不支援或跳出來源範圍的檔案應拒絕。"""
    _supported_extensions, select_log_source, _log_sources = _source_selection_api()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    unsupported_file = log_dir / "settings.json"
    unsupported_file.touch()
    (log_dir / "present.log").touch()

    with pytest.raises(ValueError):
        select_log_source(str(unsupported_file))
    with pytest.raises(FileNotFoundError):
        select_log_source(str(log_dir / "missing.log"))
    with pytest.raises(ValueError):
        select_log_source(str(log_dir), selected_files=("../outside.log",))
    with pytest.raises(FileNotFoundError):
        select_log_source(str(log_dir), selected_files=("missing.log",))


def test_selection_rejects_windows_and_posix_paths_as_filenames(tmp_path):
    """情境：檔名選擇不得接受 Windows 或 POSIX 路徑。"""
    _supported_extensions, select_log_source, _log_sources = _source_selection_api()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    for filename in (
        "../outside.log",
        r"..\outside.log",
        r"C:\outside.log",
        "C:relative.log",
        r"\\server\share.log",
        "/absolute.log",
        "nested/inside.log",
        r"nested\inside.log",
        "..",
    ):
        with pytest.raises(ValueError):
            select_log_source(str(log_dir), selected_files=(filename,))


def test_selection_rejects_unreadable_single_file_without_fallback(tmp_path, monkeypatch):
    """情境：單檔不可讀時應失敗而非退回分析整個資料夾。"""
    _supported_extensions, select_log_source, log_sources = _source_selection_api()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    selected_file = log_dir / "chosen.log"
    selected_file.touch()
    real_access = log_sources.os.access

    def deny_selected_file(path, mode):
        if str(path) == str(selected_file):
            return False
        return real_access(path, mode)

    monkeypatch.setattr(log_sources.os, "access", deny_selected_file)

    with pytest.raises(PermissionError, match="無法讀取"):
        select_log_source(str(selected_file))

