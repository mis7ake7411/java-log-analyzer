import pytest

from log_analyzer.application.source_file_selection import SourceFileSelection


def test_explicit_selection_is_preserved_until_source_changes(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for directory in (first, second):
        (directory / "app.log").write_text("one", encoding="utf-8")
        (directory / "notes.txt").write_text("two", encoding="utf-8")
    state = SourceFileSelection()
    source = state.resolve(str(first))
    assert source.filenames == ("app.log", "notes.txt")
    state.choose(source, ("app.log",))
    (first / "new.log").write_text("new", encoding="utf-8")
    assert state.resolve(str(first)).filenames == ("app.log",)
    assert state.resolve(str(second)).filenames == ("app.log", "notes.txt")
    assert state.selected_files is None


def test_missing_or_invalid_selection_is_not_silently_replaced(tmp_path):
    (tmp_path / "app.log").write_text("one", encoding="utf-8")
    state = SourceFileSelection()
    source = state.resolve(str(tmp_path))
    state.choose(source, ("app.log",))
    with pytest.raises(ValueError):
        state.choose(source, ("../outside.log",))
    assert state.selected_files == ("app.log",)
    (tmp_path / "app.log").unlink()
    with pytest.raises(FileNotFoundError):
        state.resolve(str(tmp_path))
