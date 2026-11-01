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
    """情境：CLI 套件應重新匯出 main 且標準 helper 可匯入。"""
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
    """情境：TUI 套件應匯出 App 且標準 helper 可匯入。"""
    tui = importlib.import_module("log_analyzer.presentation.tui")
    app = importlib.import_module("log_analyzer.presentation.tui.app")
    dialogs = importlib.import_module("log_analyzer.presentation.tui.screens.path_picker")
    views = importlib.import_module("log_analyzer.presentation.tui.views")
    inputs = importlib.import_module("log_analyzer.presentation.tui.form_values")
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
        "presentation/tui/dialogs.py",
        "presentation/tui/inputs.py",
        "presentation/tui/event_browser.py",
        "presentation/tui/recipes.py",
        "presentation/tui/source_scope.py",
        "presentation/tui/source_selection.py",
        "presentation/tui/result_context.py",
        "presentation/recent_form_state.py",
        "presentation/tui_dialogs.py",
        "presentation/tui_event_browser.py",
        "presentation/tui_inputs.py",
        "presentation/tui_recipes.py",
        "presentation/tui_views.py",
    )


def test_exporting_package_reexports_api_and_canonical_helpers_are_available():
    """情境：匯出套件應重新匯出 API 且標準 helper 可匯入。"""
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
    """情境：修改標準匯出器大小門檻應影響正式匯出呼叫。"""
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
    """情境：TUI 樣式表路徑應解析在套件內。"""
    app = importlib.import_module("log_analyzer.presentation.tui.app").LogAnalyzerApp

    assert all(Path(path).is_file() for path in app.CSS_PATH)


def test_application_and_domain_modules_live_in_canonical_packages():
    """搬移後的分析、事件、來源與 Logback 模組應位於對應責任套件。"""
    module_paths = {
        "log_analyzer.application.analysis.service": "application/analysis/service.py",
        "log_analyzer.application.analysis.summary": "application/analysis/summary.py",
        "log_analyzer.application.analysis.evidence": "application/analysis/evidence.py",
        "log_analyzer.application.events.query": "application/events/query.py",
        "log_analyzer.application.events.correlation": "application/events/correlation.py",
        "log_analyzer.application.sources.selection": "application/sources/selection.py",
        "log_analyzer.application.sources.preview": "application/sources/preview.py",
        "log_analyzer.infrastructure.sources.context_index": "infrastructure/sources/context_index.py",
        "log_analyzer.domain.logback.pattern": "domain/logback/pattern.py",
        "log_analyzer.domain.logback.xml": "domain/logback/xml.py",
        "log_analyzer.domain.logback.mdc": "domain/logback/mdc.py",
    }
    symbol_names = {
        "log_analyzer.application.analysis.service": (
            "AnalysisRequest",
            "AnalysisResult",
            "run_analysis_with_options",
        ),
        "log_analyzer.application.analysis.summary": (
            "DashboardAnalysis",
            "aggregate_dashboard_summary",
        ),
        "log_analyzer.application.analysis.evidence": ("export_evidence_manifest",),
        "log_analyzer.application.events.query": (
            "EventQueryFilters",
            "find_group_indices",
        ),
        "log_analyzer.application.events.correlation": (
            "CorrelatedOccurrence",
            "find_correlated_occurrences",
        ),
        "log_analyzer.application.sources.selection": ("SourceFileSelection",),
        "log_analyzer.application.sources.preview": (
            "SourcePreview",
            "read_source_context",
        ),
        "log_analyzer.infrastructure.sources.context_index": ("SourceContextReader",),
        "log_analyzer.domain.logback.pattern": (
            "UnsupportedLogbackPatternError",
            "compile_logback_pattern",
        ),
        "log_analyzer.domain.logback.xml": (
            "LogbackPatternAssessment",
            "assess_logback_patterns",
        ),
        "log_analyzer.domain.logback.mdc": ("make_mdc_group_name", "extract_mdc"),
    }
    packages = {
        "log_analyzer.application.analysis": "application/analysis",
        "log_analyzer.application.events": "application/events",
        "log_analyzer.application.sources": "application/sources",
        "log_analyzer.infrastructure.sources": "infrastructure/sources",
        "log_analyzer.domain.logback": "domain/logback",
    }

    modules = {name: importlib.import_module(name) for name in module_paths}
    for name, relative_path in module_paths.items():
        module = modules[name]
        assert Path(module.__file__).resolve() == (_SOURCE_PACKAGE / relative_path).resolve()
        assert module.__package__ == name.rpartition(".")[0]
        for symbol_name in symbol_names[name]:
            assert getattr(module, symbol_name).__module__ == name

    assert (
        modules["log_analyzer.application.sources.preview"].SourceContextReader
        is modules[
            "log_analyzer.infrastructure.sources.context_index"
        ].SourceContextReader
    )

    for name, relative_path in packages.items():
        package = importlib.import_module(name)
        package_path = (_SOURCE_PACKAGE / relative_path).resolve()
        assert Path(package.__file__).resolve() == package_path / "__init__.py"
        assert Path(next(iter(package.__path__))).resolve() == package_path
        assert package.__doc__

    _assert_legacy_flat_modules_are_absent(
        "application/analysis_service.py",
        "application/dashboard_summary.py",
        "application/evidence_manifest.py",
        "application/event_query.py",
        "application/event_correlation.py",
        "application/source_file_selection.py",
        "application/source_preview.py",
        "application/source_context_index.py",
        "domain/logback_pattern.py",
        "domain/logback_xml.py",
        "domain/logback_mdc.py",
    )
    test_root = Path(__file__).parents[1]
    assert not (test_root / "application/test_source_context_index.py").exists()
    assert (test_root / "infrastructure/test_source_context_index.py").is_file()
