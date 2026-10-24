import asyncio

from textual.widgets import Button, Input, Static

from log_analyzer.domain.logback_xml import assess_logback_patterns
from log_analyzer.presentation.tui import app as tui_module
from log_analyzer.presentation.tui import LogAnalyzerApp
from log_analyzer.presentation.tui.dialogs import LogbackPatternConfirmationScreen


PATTERN = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n"
TIED_PATTERN = PATTERN.replace("%msg", "%message")
LOG_LINE = "2026-10-24 12:00:00.123 [main] INFO  com.test.App - sample"


def _disable_recent_state(monkeypatch):
    monkeypatch.setattr(tui_module, "load_recent_tui_state", lambda: {})
    monkeypatch.setattr(tui_module, "save_recent_tui_state", lambda _state: None)


def _write_xml(path, name, pattern):
    path.write_text(
        f'<configuration><property name="{name}" value="{pattern}"/></configuration>',
        encoding="utf-8",
    )
    return path


async def _click_run_for_confirmation(app, pilot):
    await asyncio.wait_for(pilot.click("#run"), timeout=5)
    await pilot.pause()
    assert app.screen.__class__.__name__ == "LogbackPatternConfirmationScreen"


async def _cancel_confirmation(pilot):
    await asyncio.wait_for(pilot.press("escape"), timeout=5)
    await pilot.pause()


async def _wait_for_run_button_ready(app, pilot):
    run_button = app.query_one("#run", Button)

    async def wait_until_ready():
        while run_button.disabled or run_button.has_class("-active"):
            await pilot.pause(0.01)

    await asyncio.wait_for(wait_until_ready(), timeout=1)


async def _wait_for_scheduled_analysis(app, previous_task=None) -> None:
    async def wait_until_scheduled() -> asyncio.Task:
        while (task := getattr(app, "_scheduled_analysis_task", None)) is None or task is previous_task:
            await asyncio.sleep(0.01)
        return task

    task = await asyncio.wait_for(wait_until_scheduled(), timeout=5)
    await asyncio.wait_for(task, timeout=10)


def _set_log_filters(app, keyword, *levels):
    app.query_one("#keyword", Input).value = keyword
    app.query_one("#level_unrestricted").value = False
    selected = set(levels)
    for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
        app.query_one(f"#level_{level}").value = level in selected


def test_canceling_pattern_confirmation_preserves_the_previous_result(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + "\n", encoding="utf-8")
    xml_path = _write_xml(
        tmp_path / "logback.xml",
        "LOG_PATTERN",
        "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%thread] [%-5level] %logger - %msg%n",
    )

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            first_click = await asyncio.wait_for(pilot.click("#run"), timeout=5)
            assert first_click, "initial #run click did not hit its button"
            await _wait_for_scheduled_analysis(app)
            assert app._last_result is not None, f"analysis state={app._result_state}"
            previous_result = app._last_result
            assert not app._operation_in_progress
            assert not app.query_one("#run").disabled

            app.query_one("#logback_xml_path", Input).value = str(xml_path)
            await pilot.pause()
            await _wait_for_run_button_ready(app, pilot)
            run_button = app.query_one("#run", Button)
            second_click = await asyncio.wait_for(pilot.click("#run"), timeout=5)
            assert second_click, (
                "second #run click did not hit its button: "
                f"region={run_button.region!r}, size={app.size!r}, visible={run_button.visible!r}, "
                f"disabled={run_button.disabled!r}, focused={getattr(app.focused, 'id', None)!r}"
            )
            await pilot.pause()
            assert app.screen.__class__.__name__ == "LogbackPatternConfirmationScreen", (
                app._operation_in_progress,
                app._result_state,
                app.query_one("#logback-xml-status", Static).render().plain,
            )
            assert app._last_result is previous_result
            assert app._result_state == "complete"

            await asyncio.wait_for(pilot.press("escape"), timeout=5)
            await pilot.pause()

            assert app._last_result is previous_result
            assert app._result_state == "complete"
            assert not app._operation_in_progress

    asyncio.run(run_check())


def test_user_can_confirm_an_uncertain_zero_match_candidate(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + "\n", encoding="utf-8")
    xml_path = _write_xml(
        tmp_path / "logback.xml",
        "LOG_PATTERN",
        "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%thread] [%-5level] %logger - %msg%n",
    )
    submitted = []
    original_analyze_request = LogAnalyzerApp._analyze_request

    async def capture_request(app, request):
        submitted.append(request)
        return await original_analyze_request(app, request)

    monkeypatch.setattr(LogAnalyzerApp, "_analyze_request", capture_request)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            app.query_one("#logback_xml_path", Input).value = str(xml_path)
            await asyncio.wait_for(pilot.click("#run"), timeout=5)
            await pilot.pause()

            screen = app.screen
            assert screen.__class__.__name__ == "LogbackPatternConfirmationScreen"
            message = screen.query_one("#pattern-confirmation-message", Static).render().plain
            assert "0" in message
            await asyncio.wait_for(pilot.click("#pattern-select-0"), timeout=5)
            await asyncio.wait_for(pilot.pause(), timeout=5)

            assert submitted
            assert submitted[0].log_pattern == "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%thread] [%-5level] %logger - %msg%n"

    asyncio.run(run_check())


def test_auto_discovery_assesses_multiple_xml_files_together(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + "\n", encoding="utf-8")
    first_xml = _write_xml(log_dir / "logback.xml", "FIRST_PATTERN", PATTERN)
    second_xml = _write_xml(log_dir / "logback-spring.xml", "SECOND_PATTERN", TIED_PATTERN)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            await asyncio.wait_for(pilot.click("#run"), timeout=5)
            await pilot.pause()

            assert app.screen.__class__.__name__ == "LogbackPatternConfirmationScreen"
            screen_text = "\n".join(
                widget.render().plain for widget in app.screen.query(Static)
            )
            assert first_xml.name in screen_text
            assert second_xml.name in screen_text
            assert set(app._auto_logback_xml_paths) == {str(first_xml), str(second_xml)}

            await asyncio.wait_for(pilot.press("escape"), timeout=5)

    asyncio.run(run_check())


def test_auto_discovered_unsupported_xml_does_not_fall_back_to_default(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + "\n", encoding="utf-8")
    xml_path = _write_xml(
        log_dir / "logback.xml",
        "LOG_PATTERN",
        "%d [%thread] %-5level %logger - %msg %springProperty%n",
    )
    requests = []
    original_analyze_request = LogAnalyzerApp._analyze_request

    async def capture_request(app, request):
        requests.append(request)
        return await original_analyze_request(app, request)

    monkeypatch.setattr(LogAnalyzerApp, "_analyze_request", capture_request)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            first_click = await asyncio.wait_for(pilot.click("#run"), timeout=5)
            assert first_click, "initial #run click did not hit its button"
            await _wait_for_scheduled_analysis(app)

            assert not requests
            assert app.query_one("#logback_xml_path", Input).value == str(xml_path)
            assert "不支援" in app.query_one("#logback-xml-status", Static).render().plain
            status_text = app.query_one("#logback-xml-status", Static).render().plain
            assert "LOG_PATTERN" in status_text
            assert "%springProperty" in status_text
            assert app.query_one("#manual_pattern_override").value is False

    asyncio.run(run_check())


def test_auto_discovery_refreshes_xml_candidates_before_each_analysis(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + "\n", encoding="utf-8")
    first_xml = _write_xml(log_dir / "logback.xml", "FIRST_PATTERN", PATTERN)
    submitted = []
    original_analyze_request = LogAnalyzerApp._analyze_request

    async def capture_request(app, request):
        submitted.append(request)
        return await original_analyze_request(app, request)

    monkeypatch.setattr(LogAnalyzerApp, "_analyze_request", capture_request)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            first_click = await asyncio.wait_for(pilot.click("#run"), timeout=5)
            assert first_click, "initial #run click did not hit its button"
            await _wait_for_scheduled_analysis(app)

            assert len(submitted) == 1
            assert submitted[0].log_pattern == PATTERN
            assert app._auto_logback_xml_paths == (str(first_xml),)

            second_xml = _write_xml(log_dir / "logback-spring.xml", "SECOND_PATTERN", TIED_PATTERN)
            previous_task = app._scheduled_analysis_task
            await _wait_for_run_button_ready(app, pilot)
            second_click = await asyncio.wait_for(pilot.click("#run"), timeout=5)
            assert second_click, "second #run click did not hit its button"
            await _wait_for_scheduled_analysis(app, previous_task)
            await pilot.pause()

            assert app.screen.__class__.__name__ == "LogbackPatternConfirmationScreen"
            assert set(app._auto_logback_xml_paths) == {str(first_xml), str(second_xml)}
            assert len(submitted) == 1
            await asyncio.wait_for(pilot.press("escape"), timeout=5)

    asyncio.run(run_check())


async def _exercise_cancel_retry_frozen_request(log_dir, xml_path, tmp_path, assessments, submitted):
    app = LogAnalyzerApp()
    async with app.run_test(size=(90, 34)) as pilot:
        await pilot.pause()
        app.query_one("#path", Input).value = str(log_dir)
        app.query_one("#logback_xml_path", Input).value = str(xml_path)
        _set_log_filters(app, "initial-filter", "ERROR")
        await _click_run_for_confirmation(app, pilot)
        first_assessment_count = len(assessments)

        await _cancel_confirmation(pilot)
        assert not submitted
        await _click_run_for_confirmation(app, pilot)
        assert len(assessments) > first_assessment_count

        app.query_one("#path", Input).value = str(tmp_path)
        _set_log_filters(app, "changed-filter", "INFO")
        await asyncio.wait_for(pilot.click("#pattern-select-0"), timeout=5)
        await asyncio.wait_for(pilot.pause(), timeout=5)
        assert submitted
        request = submitted[0]
        assert request.path == str(log_dir)
        assert request.selected_files == ("app.log",)
        assert request.keyword == "initial-filter"
        assert request.levels == ("ERROR",)

        assessments_after_confirm = len(assessments)
        app.query_one("#path", Input).value = str(log_dir)
        _set_log_filters(app, "initial-filter", "ERROR")
        await _click_run_for_confirmation(app, pilot)
        assert len(assessments) > assessments_after_confirm
        assert len(submitted) == 1
        await _cancel_confirmation(pilot)


def test_cancel_retry_reassesses_and_confirm_uses_frozen_source_and_filters(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + "\n", encoding="utf-8")
    xml_path = _write_xml(
        tmp_path / "logback.xml",
        "LOG_PATTERN",
        "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%thread] [%-5level] %logger - %msg%n",
    )
    assessments = []
    submitted = []
    original_assessment = tui_module.assess_logback_patterns
    original_analyze_request = LogAnalyzerApp._analyze_request

    def count_assessment(*args, **kwargs):
        assessments.append(args[0])
        return original_assessment(*args, **kwargs)

    async def capture_request(app, request):
        submitted.append(request)
        return await original_analyze_request(app, request)

    monkeypatch.setattr(tui_module, "assess_logback_patterns", count_assessment)
    monkeypatch.setattr(LogAnalyzerApp, "_analyze_request", capture_request)
    asyncio.run(_exercise_cancel_retry_frozen_request(log_dir, xml_path, tmp_path, assessments, submitted))


def test_load_action_reports_uncertainty_without_opening_confirmation(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("unmatched sample\n", encoding="utf-8")
    xml_path = _write_xml(log_dir / "logback.xml", "LOG_PATTERN", PATTERN)

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            app.query_one("#logback_xml_path", Input).value = str(xml_path)

            app.action_load_logback_xml()

            status = app.query_one("#logback-xml-status", Static).render().plain
            preview = app.query_one("#pattern-preview", Static).render().plain
            assert "抽樣日誌沒有命中" in status
            assert "Pattern 資訊：" in preview
            assert "有效 Pattern：" not in preview
            assert app.screen.__class__.__name__ == "Screen"

    asyncio.run(run_check())


def test_load_action_reports_unsupported_error_without_opening_confirmation(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("sample\n", encoding="utf-8")
    xml_path = _write_xml(
        log_dir / "logback.xml",
        "UNSUPPORTED_PATTERN",
        "%d %springProperty%n",
    )

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            app.query_one("#logback_xml_path", Input).value = str(xml_path)

            app.action_load_logback_xml()

            status = app.query_one("#logback-xml-status", Static).render().plain
            preview = app.query_one("#pattern-preview", Static).render().plain
            assert "UNSUPPORTED_PATTERN" in status
            assert "%springProperty" in status
            assert "Pattern 資訊：" in preview
            assert app.screen.__class__.__name__ == "Screen"

    asyncio.run(run_check())


def test_pattern_confirmation_dialog_is_operable_at_compact_and_wide_sizes(tmp_path):
    xml_path = _write_xml(tmp_path / "logback.xml", "LOG_PATTERN", PATTERN)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + " [bold] literal\n", encoding="utf-8")
    assessment = assess_logback_patterns([str(xml_path)], str(log_dir))
    assert assessment.status == "ready"

    async def check_size(size):
        app = LogAnalyzerApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = LogbackPatternConfirmationScreen(assessment)
            app.push_screen(screen)
            await pilot.pause()

            cancel = screen.query_one("#pattern-cancel", Button)
            assert cancel.region.x >= 0
            assert cancel.region.y >= 0
            assert cancel.region.x + cancel.region.width <= size[0]
            assert cancel.region.y + cancel.region.height <= size[1]
            assert cancel.has_focus
            raw_previews = [
                widget.render().plain
                for widget in screen.query(Static)
                if widget.id is None
            ]
            assert any("[bold] literal" in text for text in raw_previews)

            await asyncio.wait_for(pilot.press("tab"), timeout=5)
            await asyncio.wait_for(pilot.press("tab"), timeout=5)
            assert app.focused is screen.query_one("#pattern-select-0", Button)
            assert app.focused.region.y + app.focused.region.height <= size[1]

    asyncio.run(check_size((70, 24)))
    asyncio.run(check_size((184, 60)))


def test_pattern_confirmation_pending_state_is_cleared_on_shutdown(tmp_path, monkeypatch):
    _disable_recent_state(monkeypatch)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(LOG_LINE + "\n", encoding="utf-8")
    xml_path = _write_xml(
        tmp_path / "logback.xml",
        "LOG_PATTERN",
        "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%thread] [%-5level] %logger - %msg%n",
    )

    async def run_check():
        app = LogAnalyzerApp()
        async with app.run_test(size=(90, 34)) as pilot:
            await pilot.pause()
            app.query_one("#path", Input).value = str(log_dir)
            app.query_one("#logback_xml_path", Input).value = str(xml_path)
            await asyncio.wait_for(pilot.click("#run"), timeout=5)
            await pilot.pause()
            assert app._pending_pattern_analysis is not None
            assert app._operation_in_progress

        assert app._pending_pattern_analysis is None
        assert not app._operation_in_progress

    asyncio.run(run_check())
