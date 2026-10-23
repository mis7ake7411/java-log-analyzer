import importlib
from pathlib import Path


_SOURCE_PACKAGE = Path(__file__).parents[2] / "src" / "log_analyzer"


def _assert_legacy_flat_modules_are_absent(*relative_paths: str) -> None:
    existing_paths = [
        _SOURCE_PACKAGE / relative_path
        for relative_path in relative_paths
        if (_SOURCE_PACKAGE / relative_path).exists()
    ]

    assert existing_paths == []


def test_cli_package_reexports_main_and_canonical_helpers_are_available():
    cli = importlib.import_module("log_analyzer.presentation.cli")
    entrypoint = importlib.import_module("log_analyzer.presentation.cli.entrypoint")
    args = importlib.import_module("log_analyzer.presentation.cli.args")
    runtime = importlib.import_module("log_analyzer.presentation.cli.runtime")

    assert cli.main is entrypoint.main
    assert callable(args.build_argument_parser)
    assert callable(runtime.resolve_levels)
    assert not Path(entrypoint.__file__).parents[1].joinpath("cli.py").exists()
    _assert_legacy_flat_modules_are_absent(
        "presentation/cli_args.py",
        "presentation/cli_runtime.py",
    )


def test_tui_package_exports_app_and_canonical_helpers_are_available():
    tui = importlib.import_module("log_analyzer.presentation.tui")
    app = importlib.import_module("log_analyzer.presentation.tui.app")
    dialogs = importlib.import_module("log_analyzer.presentation.tui.dialogs")
    views = importlib.import_module("log_analyzer.presentation.tui.views")
    inputs = importlib.import_module("log_analyzer.presentation.tui.inputs")
    paths = importlib.import_module("log_analyzer.infrastructure.paths")

    assert tui.__all__ == ["LogAnalyzerApp"]
    assert tui.LogAnalyzerApp is app.LogAnalyzerApp
    assert all(
        callable(getattr(dialogs, name))
        for name in ("DirectoryPickerScreen", "FilePickerScreen")
    )
    assert all(
        callable(getattr(views, name))
        for name in ("build_dashboard_view", "build_error_view", "build_loading_view")
    )
    assert all(
        callable(getattr(inputs, name))
        for name in (
            "get_default_end_date_text",
            "get_default_end_time_text",
            "get_default_output_name_text",
            "get_default_start_date_text",
            "get_default_start_time_text",
            "parse_datetime_range_inputs",
        )
    )
    assert callable(paths.get_system_root_path)
    assert not Path(app.__file__).parents[1].joinpath("tui.py").exists()
    _assert_legacy_flat_modules_are_absent(
        "presentation/recent_form_state.py",
        "presentation/tui_dialogs.py",
        "presentation/tui_event_browser.py",
        "presentation/tui_inputs.py",
        "presentation/tui_recipes.py",
        "presentation/tui_views.py",
    )


def test_exporting_package_reexports_api_and_canonical_helpers_are_available():
    exporting = importlib.import_module("log_analyzer.infrastructure.exporting")
    exporter = importlib.import_module("log_analyzer.infrastructure.exporting.exporter")
    fields = importlib.import_module("log_analyzer.infrastructure.exporting.fields")

    assert exporting.ExportOptions is exporter.ExportOptions
    assert exporting.export_results is exporter.export_results
    assert exporting.export_with_options is exporter.export_with_options
    assert callable(fields.normalize_export_fields)
    _assert_legacy_flat_modules_are_absent(
        "infrastructure/exporter.py",
        "infrastructure/export_csv.py",
        "infrastructure/export_json.py",
        "infrastructure/export_markdown.py",
        "infrastructure/export_fields.py",
    )


def test_exporter_limit_patch_controls_canonical_export_call(tmp_path, monkeypatch):
    canonical_exporter = importlib.import_module("log_analyzer.infrastructure.exporting.exporter")
    monkeypatch.setattr(canonical_exporter, "MAX_EXPORT_BYTES_PER_FILE", 5_000)
    output_path = tmp_path / "report.csv"
    matched_logs = [
        {
            "count": 1,
            "line_numbers": [index],
            "timestamp": f"2026-06-07 12:00:{index:02d}.000",
            "last_timestamp": f"2026-06-07 12:00:{index:02d}.000",
            "level": "INFO",
            "thread": "main",
            "logger": "com.test.App",
            "filename": "app.log",
            "message": "X" * 900,
            "message_body": "X" * 900,
            "stacktrace": "",
        }
        for index in range(1, 4)
    ]

    files = canonical_exporter.export_results({"INFO": 3}, matched_logs, str(output_path), "csv")

    assert files == [
        str(tmp_path / "report_summary.csv"),
        str(tmp_path / "report_part001.csv"),
        str(tmp_path / "report_part002.csv"),
    ]


def test_tui_package_stylesheets_resolve_inside_log_analyzer_package():
    app = importlib.import_module("log_analyzer.presentation.tui.app").LogAnalyzerApp

    assert all(Path(path).is_file() for path in app.CSS_PATH)
