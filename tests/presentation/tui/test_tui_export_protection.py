import asyncio
import json

from textual.widgets import Input, Static

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


async def _analyze_and_open_export(app, pilot, logs, output_dir):
    await pilot.pause()
    app.query_one("#path").value = str(logs)
    app.query_one("#output_path").value = str(output_dir)
    app.query_one("#output_name").value = "captured"
    app.query_one("#format").value = "json"
    await app.action_run_analysis()
    await pilot.pause()
    assert app._last_result is not None
    result = app._last_result
    app.action_show_export_settings()
    await pilot.pause()
    return result


async def _wait_for_export_to_finish(app, pilot):
    for _ in range(300):
        if not app._operation_in_progress:
            return
        await pilot.pause(0.01)
    raise AssertionError("export operation did not finish")


def _assert_result_retained(app, result, store):
    assert app._last_result is result
    assert result.matched_logs is store
    assert len(store) == 1


def test_export_conflict_cancel_preserves_existing_file_and_retained_result(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "captured before export")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    report_path = export_dir / "captured.json"
    report_path.write_bytes(b"previous report bytes")
    monkeypatch.chdir(tmp_path)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            result = await _analyze_and_open_export(app, pilot, logs, export_dir)
            store = result.matched_logs
            original_output_path = result.output_path
            original_format = result.format_name
            original_exported_files = list(result.exported_files)
            base_screen = app.screen

            await app.action_confirm_export()
            await pilot.pause()

            assert app.screen is not base_screen
            paths = app.screen.query_one("#export-conflict-paths", Static).render().plain
            assert str(report_path.resolve()) in paths
            assert app.focused.id == "export-conflict-cancel"
            assert report_path.read_bytes() == b"previous report bytes"

            await pilot.press("escape")
            await pilot.pause()

            assert app.screen is base_screen
            assert report_path.read_bytes() == b"previous report bytes"
            assert result.output_path == original_output_path
            assert result.format_name == original_format
            assert result.exported_files == original_exported_files
            assert "匯出完成" not in app.query_one("#export-status", Static).render().plain
            _assert_result_retained(app, result, store)

    asyncio.run(run_check())


def test_export_conflict_rename_selects_name_and_writes_only_after_user_retries(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "captured before rename")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    report_path = export_dir / "captured.json"
    report_path.write_bytes(b"previous report bytes")
    monkeypatch.chdir(tmp_path)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            result = await _analyze_and_open_export(app, pilot, logs, export_dir)
            store = result.matched_logs
            base_screen = app.screen

            await app.action_confirm_export()
            await pilot.pause()
            await pilot.click("#export-conflict-rename")
            await pilot.pause()

            name_input = app.query_one("#output_name", Input)
            assert app.screen is base_screen
            assert app.focused is name_input
            assert name_input.selection.start == 0
            assert name_input.selection.end == len(name_input.value)
            assert report_path.read_bytes() == b"previous report bytes"
            assert list(export_dir.iterdir()) == [report_path]
            _assert_result_retained(app, result, store)

            name_input.value = "renamed"
            await app.action_confirm_export()
            await pilot.pause()

            renamed_path = export_dir / "renamed.json"
            assert renamed_path.is_file()
            assert report_path.read_bytes() == b"previous report bytes"
            assert result.exported_files == [str(renamed_path.resolve())]
            _assert_result_retained(app, result, store)

    asyncio.run(run_check())


def test_export_conflict_overwrite_retries_the_frozen_request(tmp_path, monkeypatch):
    logs, source = _write_log(tmp_path, "captured before modal")
    export_dir = tmp_path / "exports"
    edited_dir = tmp_path / "edited-exports"
    export_dir.mkdir()
    edited_dir.mkdir()
    report_path = export_dir / "captured.json"
    report_path.write_bytes(b"previous report bytes")
    monkeypatch.chdir(tmp_path)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            result = await _analyze_and_open_export(app, pilot, logs, export_dir)
            store = result.matched_logs
            name_input = app.query_one("#output_name", Input)
            output_path_input = app.query_one("#output_path", Input)
            app.query_one("#include_manifest").value = True
            base_screen = app.screen

            await app.action_confirm_export()
            await pilot.pause()
            assert app.screen is not base_screen
            assert report_path.read_bytes() == b"previous report bytes"

            # Form changes during the modal must not alter the in-flight retry.
            name_input.value = "edited-after-click"
            output_path_input.value = str(edited_dir)
            app.query_one("#format").value = "csv"
            source.write_text(
                "2026-10-22 12:00:00.000 [main] ERROR com.test.App - changed after analysis\n",
                encoding="utf-8",
            )
            app.query_one("#include_manifest").value = False
            await pilot.resize_terminal(110, 45)
            await pilot.pause()

            await pilot.click("#export-conflict-overwrite")
            await _wait_for_export_to_finish(app, pilot)

            manifest_path = export_dir / "captured_manifest.json"
            assert app.screen is base_screen
            exported_results = json.loads(
                report_path.read_text(encoding="utf-8")
            )["results"]
            assert len(exported_results) == 1
            assert exported_results[0]["level"] == "ERROR"
            assert exported_results[0]["message"] == "captured before modal"
            assert manifest_path.is_file()
            assert list(edited_dir.iterdir()) == []
            assert result.exported_files == [str(report_path.resolve())]
            assert app.query_one("#export-status", Static).render().plain.endswith(
                str(manifest_path.resolve())
            )
            _assert_result_retained(app, result, store)

    asyncio.run(run_check())


def test_manifest_only_conflict_lists_manifest_and_does_not_publish_report(tmp_path, monkeypatch):
    logs, _ = _write_log(tmp_path, "must not be partially published")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    manifest_path = export_dir / "captured_manifest.json"
    manifest_path.write_bytes(b"previous manifest bytes")
    monkeypatch.chdir(tmp_path)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(140, 60)) as pilot:
            result = await _analyze_and_open_export(app, pilot, logs, export_dir)
            store = result.matched_logs
            app.query_one("#include_manifest").value = True

            await app.action_confirm_export()
            await pilot.pause()

            paths = app.screen.query_one("#export-conflict-paths", Static).render().plain
            assert str(manifest_path.resolve()) in paths
            assert str((export_dir / "captured.json").resolve()) not in paths
            assert not (export_dir / "captured.json").exists()
            assert manifest_path.read_bytes() == b"previous manifest bytes"
            assert result.exported_files == []
            assert result.output_path == ""
            _assert_result_retained(app, result, store)

            await pilot.click("#export-conflict-cancel")
            await pilot.pause()

    asyncio.run(run_check())


def test_export_conflict_dialog_fits_narrow_and_wide_terminals_and_handles_escape(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    levels = ("ERROR", "WARN", "INFO", "DEBUG", "TRACE")
    for index, level in enumerate(levels):
        (logs / f"{level.lower()}.log").write_text(
            f"2026-10-22 12:00:0{index}.000 [main] {level} com.test.App - {level} event\n",
            encoding="utf-8",
        )
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    output_name = "many-conflicts"
    for suffix in ("summary", *levels):
        (export_dir / f"{output_name}_{suffix}.csv").write_text(
            "keep existing output\n",
            encoding="utf-8",
        )
    monkeypatch.chdir(tmp_path)

    async def run_check(size, use_escape):
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            app.query_one("#path").value = str(logs)
            app.query_one("#output_path").value = str(export_dir)
            app.query_one("#output_name").value = output_name
            app.query_one("#format").value = "csv"
            app.query_one("#split_by_level").value = True
            app.query_one("#level_unrestricted").value = True
            await app.action_run_analysis()
            await pilot.pause()
            assert app._last_result is not None
            result = app._last_result
            app.action_show_export_settings()
            await pilot.pause()
            base_screen = app.screen

            await app.action_confirm_export()
            await pilot.pause()

            dialog = app.screen
            card = dialog.query_one("#export-conflict-card")
            paths = dialog.query_one("#export-conflict-paths", Static)
            assert len([line for line in paths.render().plain.splitlines() if line.strip()]) > 3
            if size[0] <= 70:
                assert paths.parent.max_scroll_y > 0
            assert card.region.y >= 0
            assert card.region.y + card.region.height <= app.size.height
            for selector in (
                "#export-conflict-rename",
                "#export-conflict-overwrite",
                "#export-conflict-cancel",
            ):
                button = dialog.query_one(selector)
                assert button.region.width > 0
                assert button.region.height > 0
                assert button.region.y >= 0
                assert button.region.y + button.region.height <= app.size.height

            if use_escape:
                await pilot.press("escape")
            else:
                await pilot.click("#export-conflict-cancel")
            await pilot.pause()
            assert app.screen is base_screen
            assert app._last_result is result

    asyncio.run(run_check((70, 24), True))
    asyncio.run(run_check((184, 60), False))
