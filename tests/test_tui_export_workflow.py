import asyncio
import csv
import io
import json
from types import SimpleNamespace

from log_analyzer.infrastructure.export_csv import render_csv_prefix
from log_analyzer.infrastructure.export_fields import EXPORT_FIELDS
from log_analyzer.presentation import tui as tui_module
from log_analyzer.presentation.recent_form_state import load_recent_tui_state
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


def _select_export_fields(app, selected: tuple[str, ...]) -> None:
    for field in EXPORT_FIELDS:
        app.query_one(f"#export_field_{field}").value = field in selected


def test_analysis_succeeds_with_missing_export_directory_without_creating_report(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "captured before export")
    missing_export_dir = tmp_path / "not-created-until-export"
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(logs)
            app.query_one("#output_path").value = str(missing_export_dir)
            app.query_one("#output_name").value = "must-not-exist"

            await pilot.click("#run")
            await pilot.pause()

            result = app._last_result
            assert result is not None
            assert result.analysis_request.path == str(logs.resolve())
            assert result.exported_files == []
            assert not missing_export_dir.exists()
            assert not (tmp_path / "must-not-exist.csv").exists()
            assert app.query_one("#export-settings").display is False
            assert app.query_one("#export-result").disabled is False
            assert all(app.query_one(f"#export_field_{field}").value for field in EXPORT_FIELDS)

    asyncio.run(run_check())


def test_enter_from_export_controls_exports_retained_analysis_after_source_changes(tmp_path, monkeypatch):
    logs, source = _write_log(tmp_path, "captured before export")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(logs)
            app.query_one("#output_path").value = str(export_dir)
            app.query_one("#output_name").value = "retained"

            await pilot.click("#run")
            await pilot.pause()
            result = app._last_result
            assert result is not None
            assert result.exported_files == []
            assert list(export_dir.iterdir()) == []

            await pilot.click("#export-result")
            await pilot.pause()
            assert app.query_one("#export-settings").display is True
            assert list(export_dir.iterdir()) == []

            app.query_one("#keyword").value = "changed after scan"
            app.query_one("#format").value = "json"
            _select_export_fields(app, ("level", "message"))
            source.write_text(
                "2026-10-22 12:00:00.000 [main] ERROR com.test.App - changed after scan\n",
                encoding="utf-8",
            )

            app.query_one("#output_name").focus()
            await pilot.press("enter")
            await pilot.pause()

            report_path = export_dir / "retained.json"
            assert report_path.exists()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            assert report["results"] == [
                {"level": "ERROR", "message": "captured before export"}
            ]
            assert app._last_result is result
            assert result.analysis_request.keyword is None
            assert result.output_path == str(report_path.resolve())
            assert result.format_name == "json"

    asyncio.run(run_check())


def test_default_export_keeps_legacy_all_fields_report_layout(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "default full report")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    captured_options = []
    real_export = tui_module.export_analysis_result

    def capture_export(result, options):
        captured_options.append(options)
        return real_export(result, options)

    monkeypatch.setattr(tui_module, "export_analysis_result", capture_export)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(logs)
            app.query_one("#output_path").value = str(export_dir)
            app.query_one("#output_name").value = "default"

            await pilot.click("#run")
            await pilot.pause()
            await pilot.click("#export-result")
            await pilot.pause()
            await pilot.click("#confirm-export")
            await pilot.pause()

            assert app._last_result is not None
            assert len(captured_options) == 1
            assert captured_options[0].fields is None

            with (export_dir / "default.csv").open(encoding="utf-8-sig", newline="") as report:
                rows = list(csv.reader(report))
            legacy_rows = list(csv.reader(io.StringIO(render_csv_prefix({}))))
            legacy_header = next(row for row in legacy_rows if len(row) == len(EXPORT_FIELDS))
            assert legacy_header in rows

    asyncio.run(run_check())


def _assert_result_store_retained(app, result, store) -> None:
    assert app._last_result is result
    assert len(store) == 1


def _assert_path_failure_status(status: str, previous_status: str, path) -> None:
    assert status != previous_status
    assert str(path.resolve()) in status


def test_export_validation_failure_keeps_result_for_retry(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "retry this export")
    bad_output_path = tmp_path / "not-a-directory[red]"
    bad_output_path.write_text("file", encoding="utf-8")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    async def run_check() -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(logs)
            await pilot.click("#run")
            await pilot.pause()

            result = app._last_result
            assert result is not None
            store = result.matched_logs
            assert len(store) == 1

            await pilot.click("#export-result")
            await pilot.pause()
            _select_export_fields(app, ())
            await pilot.click("#confirm-export")
            await pilot.pause()

            empty_field_status = app.query_one("#export-status").render().plain
            assert "欄位" in empty_field_status
            _assert_result_store_retained(app, result, store)

            _select_export_fields(app, ("level", "message"))
            app.query_one("#output_path").value = str(bad_output_path)
            app.query_one("#format").value = "json"
            app.query_one("#split_by_level").value = True
            await pilot.pause()
            app.query_one("#output_name").focus()
            await pilot.press("enter")
            await pilot.pause()

            failed_path_status = app.query_one("#export-status").render().plain
            _assert_path_failure_status(failed_path_status, empty_field_status, bad_output_path)
            _assert_result_store_retained(app, result, store)
            saved_preferences = load_recent_tui_state()
            assert saved_preferences["output_path"] == str(bad_output_path)
            assert saved_preferences["format"] == "json"
            assert saved_preferences["split_by_level"] is True
            assert "output_name" not in saved_preferences

            app.query_one("#output_path").value = str(export_dir)
            app.query_one("#output_name").value = "retry"
            await pilot.pause()
            app.query_one("#output_name").focus()
            await pilot.press("enter")
            await pilot.pause()

            assert (export_dir / "retry.json").exists()
            _assert_result_store_retained(app, result, store)

    asyncio.run(run_check())


def test_export_controls_are_reachable_in_narrow_and_wide_layouts():
    async def run_check(size: tuple[int, int]) -> None:
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            result = SimpleNamespace(total_logs=1, matched_occurrences=1)
            app._last_result = result
            app._set_result_status("complete", result)

            await pilot.click("#export-result")
            await pilot.pause()

            settings = app.query_one("#export-settings")
            confirm = app.query_one("#confirm-export")
            assert settings.display is True
            assert confirm.region.height > 0
            assert confirm.region.width > 0
            assert confirm.region.y >= 0
            assert confirm.region.y + confirm.region.height <= app.size.height

    asyncio.run(run_check((70, 40)))
    asyncio.run(run_check((184, 90)))
