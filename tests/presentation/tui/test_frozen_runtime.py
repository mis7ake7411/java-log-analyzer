import sys

import pytest

from log_analyzer import version
from log_analyzer.infrastructure.recipe_store import get_recipe_store_path
from log_analyzer.presentation.tui import recent_state


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_frozen_recent_state_is_shared_between_working_directories(tmp_path, monkeypatch, platform):
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
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    recent_state.save_recent_tui_state({"path": "source-logs"})
    assert recent_state.get_recent_tui_state_path().parent == tmp_path
    assert recent_state.load_recent_tui_state() == {"path": "source-logs"}


def test_frozen_version_uses_bundled_metadata_instead_of_adjacent_project(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(version, "_get_source_version", lambda: "9.9.9")
    monkeypatch.setattr(version, "package_version", lambda _name: "1.0.17")
    assert version.get_package_version() == "1.0.17"
