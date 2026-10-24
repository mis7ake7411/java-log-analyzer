from log_analyzer.presentation.tui.controllers import exporting as exporting_module
import asyncio
import hashlib
import json
import threading
from pathlib import Path

from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp


def _write_log(tmp_path, message: str):
    logs = tmp_path / "logs"
    logs.mkdir()
    source = logs / "app.log"
    source.write_text(
        f"2026-10-22 12:00:00.000 [main] ERROR com.test.App - {message}\n",
        encoding="utf-8",
    )
    return logs, source


async def _analyze_and_open_export(app, pilot, logs, export_dir, keyword: str):
    app.query_one("#path").value = str(logs)
    app.query_one("#keyword").value = keyword
    app.query_one("#output_path").value = str(export_dir)
    app.query_one("#output_name").value = "captured"
    app.query_one("#format").value = "json"

    await app.action_run_analysis()
    await pilot.pause()
    assert app._last_result is not None
    result = app._last_result
    app.action_show_export_settings()
    await pilot.pause()
    return result


async def _edit_export_form_after_export_starts(app, export_started, continue_export, edited_export_dir):
    export_task = asyncio.create_task(app.action_confirm_export())
    try:
        assert await asyncio.to_thread(export_started.wait, 5)
        app.query_one("#output_path").value = str(edited_export_dir)
        app.query_one("#output_name").value = "edited-after-click"
        app.query_one("#format").value = "csv"
        app.query_one("#include_manifest").value = False
    finally:
        continue_export.set()
    await export_task


def test_manifest_is_opt_in_and_default_export_creates_reports_only(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "captured report only")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    manifest_calls = []

    def fail_if_called(*args, **kwargs):
        manifest_calls.append((args, kwargs))
        raise AssertionError("default export must not create a bundle")

    monkeypatch.setattr(exporting_module, "export_analysis_bundle", fail_if_called)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            result = await _analyze_and_open_export(app, pilot, logs, export_dir, "captured")
            include_manifest = app.query_one("#include_manifest")
            assert include_manifest.value is False

            await app.action_confirm_export()
            await pilot.pause()

            report_path = export_dir / "captured.json"
            assert report_path.exists()
            assert not (export_dir / "captured_manifest.json").exists()
            assert result.exported_files == [str(report_path.resolve())]
            assert manifest_calls == []

    asyncio.run(run_check())


def test_export_click_freezes_report_options_and_manifest_choice(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "frozen export options")
    original_export_dir = tmp_path / "original-exports"
    edited_export_dir = tmp_path / "edited-exports"
    original_export_dir.mkdir()
    edited_export_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    export_started = threading.Event()
    continue_export = threading.Event()
    real_bundle = exporting_module.export_analysis_bundle
    bundle_calls = []

    def blocking_bundle(result, options):
        bundle_calls.append((result, options))
        export_started.set()
        if not continue_export.wait(timeout=5):
            raise TimeoutError("test did not release the export bundle")
        return real_bundle(result, options)

    monkeypatch.setattr(exporting_module, "export_analysis_bundle", blocking_bundle)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            result = await _analyze_and_open_export(app, pilot, logs, original_export_dir, "frozen")
            app.query_one("#output_name").value = "captured-at-click"
            app.query_one("#format").value = "json"
            app.query_one("#include_manifest").value = True

            await _edit_export_form_after_export_starts(
                app,
                export_started,
                continue_export,
                edited_export_dir,
            )
            await pilot.pause()

            report_path = original_export_dir / "captured-at-click.json"
            manifest_path = original_export_dir / "captured-at-click_manifest.json"
            assert report_path.exists()
            assert manifest_path.exists()
            assert not list(edited_export_dir.iterdir())
            assert bundle_calls[0][0] is result
            assert (
                bundle_calls[0][1].output_path == str(report_path.resolve())
                and bundle_calls[0][1].fmt == "json"
            )

    asyncio.run(run_check())


def test_manifest_uses_frozen_analysis_and_matches_exported_report_hash(tmp_path, monkeypatch):
    logs, source = _write_log(tmp_path, "captured before source edit")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    real_bundle = exporting_module.export_analysis_bundle
    bundle_calls = []

    def capture_bundle(result, options):
        bundle_calls.append((result, options))
        return real_bundle(result, options)

    monkeypatch.setattr(exporting_module, "export_analysis_bundle", capture_bundle)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            result = await _analyze_and_open_export(app, pilot, logs, export_dir, "captured")
            request = result.analysis_request
            result_store = result.matched_logs
            app.query_one("#include_manifest").value = True

            app.query_one("#keyword").value = "edited after analysis"
            app.query_one("#format").value = "csv"
            source.write_text(
                "2026-10-22 12:00:00.000 [main] ERROR com.test.App - changed after analysis\n",
                encoding="utf-8",
            )
            await app.action_confirm_export()
            await pilot.pause()

            report_path = export_dir / "captured.csv"
            manifest_path = export_dir / "captured_manifest.json"
            assert report_path.exists() and manifest_path.exists()
            manifest_text = manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            report_text = report_path.read_text(encoding="utf-8")
            assert "captured before source edit" in report_text and "changed after analysis" not in report_text
            assert "captured before source edit" not in manifest_text
            assert manifest["analysis"]["filters"]["keyword"] == request.keyword == "captured"
            assert (
                manifest["sources"][0]["verification_status"] == "changed"
                and manifest["outputs"][0]["sha256"]
                == hashlib.sha256(report_path.read_bytes()).hexdigest()
            )
            assert result.exported_files == [str(report_path.resolve())] and app._last_result is result
            assert result.matched_logs is result_store and len(result_store) == 1
            assert bundle_calls[0][0] is result
            assert bundle_calls[0][1].output_path == str(report_path.resolve())
            assert result.exported_files == [str(report_path.resolve())]

            status = app.query_one("#export-status").render().plain
            assert str(report_path.resolve()) in status and str(manifest_path.resolve()) in status
            await pilot.resize_terminal(110, 45)
            await pilot.pause()
            assert app.query_one("#export-status").render().plain == status

            app.action_clear_result()
            assert app.query_one("#export-status").render().plain == ""

    asyncio.run(run_check())


def test_bundle_failure_is_atomic_and_retry_uses_same_result(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "retryable captured report")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    real_analyze = tui_module.analyze_logs
    real_bundle = exporting_module.export_analysis_bundle
    analysis_calls = []
    bundle_calls = []

    def capture_analysis(request, *, control=None):
        analysis_calls.append(request)
        return real_analyze(request, control=control)

    def fail_once(result, options):
        bundle_calls.append((result, options))
        if len(bundle_calls) == 1:
            raise OSError("[red]bundle write blocked[/red]")
        return real_bundle(result, options)

    monkeypatch.setattr(tui_module, "analyze_logs", capture_analysis)
    monkeypatch.setattr(exporting_module, "export_analysis_bundle", fail_once)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            result = await _analyze_and_open_export(app, pilot, logs, export_dir, "retryable")
            result_store = result.matched_logs
            app.query_one("#include_manifest").value = True

            await app.action_confirm_export()
            await pilot.pause()

            report_path = export_dir / "captured.json"
            manifest_path = export_dir / "captured_manifest.json"
            failure_status = app.query_one("#export-status").render().plain
            assert "匯出失敗" in failure_status
            assert "匯出完成" not in failure_status
            assert not report_path.exists() and not manifest_path.exists()
            assert app.query_one("#browse-events").disabled is False
            assert app.query_one("#export-result").disabled is False
            assert app._last_result is result
            assert result.matched_logs is result_store
            assert len(result_store) == 1

            await app.action_confirm_export()
            await pilot.pause()

            assert manifest_path.exists()
            assert len(analysis_calls) == 1
            assert len(bundle_calls) == 2
            assert all(call[0] is result for call in bundle_calls)
            assert bundle_calls[0][1] == bundle_calls[1][1]
            assert app._last_result is result
            assert result.matched_logs is result_store
            assert len(result_store) == 1
            assert "證據清單：" in app.query_one("#export-status").render().plain

    asyncio.run(run_check())


def test_bundle_retry_keeps_auto_generated_report_name(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "retry keeps the first report name")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    real_bundle = exporting_module.export_analysis_bundle
    generated_names = []
    bundle_output_paths = []

    def next_default_name():
        name = f"analysis_retry_{len(generated_names) + 1}"
        generated_names.append(name)
        return name

    def fail_bundle_once(result, options):
        bundle_output_paths.append(Path(options.output_path))
        if len(bundle_output_paths) == 1:
            raise OSError("bundle write blocked")
        return real_bundle(result, options)

    monkeypatch.setattr(tui_module, "get_default_output_name_text", next_default_name)
    monkeypatch.setattr(exporting_module, "export_analysis_bundle", fail_bundle_once)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            await _analyze_and_open_export(app, pilot, logs, export_dir, "retry")
            initial_name = app._auto_output_name
            app.query_one("#output_name").value = initial_name
            app.query_one("#include_manifest").value = True

            await app.action_confirm_export()
            await pilot.pause()

            report_path = export_dir / f"{initial_name}.json"
            assert not report_path.exists()
            assert app.query_one("#output_name").value == initial_name
            assert generated_names == [initial_name]

            await app.action_confirm_export()
            await pilot.pause()

            assert report_path.exists()
            assert (export_dir / f"{initial_name}_manifest.json").exists()
            assert bundle_output_paths == [report_path, report_path]
            assert generated_names == [initial_name, "analysis_retry_2"]
            assert app.query_one("#output_name").value == "analysis_retry_2"

    asyncio.run(run_check())


def test_report_export_failure_does_not_attempt_manifest_and_keeps_result(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "report export failure")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    bundle_calls = []

    def fail_report(*_args, **_kwargs):
        raise OSError("[red]report write blocked[/red]")

    def fail_bundle(*args, **kwargs):
        bundle_calls.append((args, kwargs))
        raise AssertionError("bundle must not run when manifest is not requested")

    monkeypatch.setattr(exporting_module, "export_analysis_result", fail_report)
    monkeypatch.setattr(exporting_module, "export_analysis_bundle", fail_bundle)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            result = await _analyze_and_open_export(app, pilot, logs, export_dir, "report")
            result_store = result.matched_logs

            await app.action_confirm_export()
            await pilot.pause()

            assert bundle_calls == []
            assert "[red]report write blocked[/red]" in app.query_one("#export-status").render().plain
            assert app._last_result is result
            assert result.matched_logs is result_store
            assert len(result_store) == 1
            assert app.query_one("#browse-events").disabled is False

    asyncio.run(run_check())
