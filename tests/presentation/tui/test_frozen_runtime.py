import sys

import pytest

from log_analyzer import version
from log_analyzer.infrastructure.recipe_store import get_recipe_store_path
from log_analyzer.presentation.tui import recent_state


@pytest.mark.parametrize("platform", ["win32", "linux"], ids=['platform-win32', 'platform-linux'])
def test_frozen_recent_state_is_shared_between_working_directories(tmp_path, monkeypatch, platform):
    """情境：Windows 與 Linux 可攜版最近設定應跨工作目錄共用。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("APPDATA", str(tmp_path / "settings"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    recent_state.save_recent_tui_state({"path": "selected-logs", "keyword": "portable"})
    saved_path = recent_state.get_recent_tui_state_path()
    monkeypatch.chdir(second)

    assert recent_state.get_recent_tui_state_path() == saved_path
    assert saved_path.parent == get_recipe_store_path().parent
    assert recent_state.load_recent_tui_state() == {"path": "selected-logs", "keyword": "portable"}
    assert list(first.iterdir()) == []
    assert list(second.iterdir()) == []


def test_source_recent_state_keeps_existing_working_directory_behavior(tmp_path, monkeypatch):
    """情境：原始碼啟動應保留依工作目錄保存最近設定的舊行為。"""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    recent_state.save_recent_tui_state({"path": "source-logs"})
    assert recent_state.get_recent_tui_state_path().parent == tmp_path
    assert recent_state.load_recent_tui_state() == {"path": "source-logs"}


def test_frozen_version_uses_bundled_metadata_instead_of_adjacent_project(tmp_path, monkeypatch):
    """情境：可攜版版本應取自內含套件資訊而非旁邊的 pyproject。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(version, "_get_source_version", lambda: "9.9.9")
    monkeypatch.setattr(version, "package_version", lambda _name: "1.0.17")
    assert version.get_package_version() == "1.0.17"
