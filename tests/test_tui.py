from pathlib import Path
from importlib.metadata import version as package_version

from log_analyzer.tui import DirectoryPickerScreen, LogAnalyzerApp, get_package_version, get_system_root_path


def test_get_system_root_path_returns_current_drive_root():
    expected_root = Path(Path.cwd().anchor or "/")

    assert get_system_root_path() == expected_root


def test_directory_picker_root_starts_from_system_root():
    screen = DirectoryPickerScreen(
        initial_path=r"C:\not-used",
        title="選擇 Log 目錄",
        hint="",
        confirm_label="使用此路徑",
    )

    root_path, message = screen._pick_tree_root()

    assert root_path == Path(Path.cwd().anchor or "/")
    assert message == ""


def test_clear_keyword_action_empties_keyword_input():
    class FakeInput:
        def __init__(self) -> None:
            self.value = "RuntimeException"
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    app = LogAnalyzerApp()
    fake_input = FakeInput()
    app.query_one = lambda *_args, **_kwargs: fake_input  # type: ignore[assignment]

    app.action_clear_keyword()

    assert fake_input.value == ""
    assert fake_input.focused is True


def test_get_package_version_matches_installed_package():
    assert get_package_version() == package_version("java-log-analyzer")
