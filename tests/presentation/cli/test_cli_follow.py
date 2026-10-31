import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from log_analyzer.application.live.runner import LiveRunner
from log_analyzer.presentation.cli import entrypoint as cli
from log_analyzer.presentation.cli import follow
from log_analyzer.presentation.cli.args import (
    build_argument_parser,
    validate_follow_arguments,
)


def _log(message: str, level: str = "ERROR") -> bytes:
    return f"2026-10-31 12:00:00.000 [main] {level} com.test - {message}\n".encode()


async def _wait_for_condition(changed, predicate, description, timeout=6):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if predicate():
            return
        changed.clear()
        if predicate():
            continue
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"等待逾時：{description}")
        try:
            await asyncio.wait_for(changed.wait(), timeout=remaining)
        except TimeoutError as error:
            raise AssertionError(f"等待逾時：{description}") from error


def test_follow_flag_is_opt_in_and_preserves_batch_format_default():
    """情境：follow 必須明確啟用，既有批次格式預設仍為 CSV。"""
    parser = build_argument_parser(lambda: "1.0.0")

    batch_args = parser.parse_args([])
    follow_args = parser.parse_args(["--follow"])

    assert batch_args.format == "csv"
    assert batch_args.follow is False
    assert follow_args.follow is True


def test_follow_flag_is_documented_in_help():
    """情境：CLI 說明應列出持續追蹤參數。"""
    parser = build_argument_parser(lambda: "1.0.0")

    assert "--follow" in parser.format_help()


def test_follow_rejects_explicit_batch_export_and_tui_options():
    """情境：follow 不接受會改變批次輸出的參數或 TUI。"""
    incompatible_options = (
        ["-f", "json"],
        ["--format=json"],
        ["-fjson"],
        ["-o", "result.csv"],
        ["--output=result.csv"],
        ["-oresult.csv"],
        ["--sort", "level"],
        ["--sort=level"],
        ["--max-export-mb", "5"],
        ["--max-export-mb=5"],
        ["--split-by-level"],
        ["--overwrite"],
        ["--tui"],
    )

    for options in incompatible_options:
        try:
            validate_follow_arguments(["--follow", *options])
        except ValueError as exc:
            assert "follow" in str(exc).lower()
        else:
            raise AssertionError(f"未拒絕互斥參數：{options}")


def test_follow_validator_ignores_option_text_inside_values_and_after_separator():
    """情境：一般文字值及 -- 後的來源名稱不應被誤認為匯出選項。"""
    validate_follow_arguments(["--follow", "--keyword=--sort", "--", "-fjson"])


@pytest.mark.parametrize(
    "option",
    ["--out=report.csv", "--form=json", "--sor=time", "-ifjson"],
)
def test_follow_rejects_argparse_long_abbreviations_and_short_clusters(option):
    """情境：follow 也要拒絕 argparse 接受的批次旗標縮寫與短旗標組合。"""
    with pytest.raises(ValueError, match="--follow"):
        parser = build_argument_parser(lambda: "1.0.0")
        parser.parse_args(["--follow", option])
        validate_follow_arguments(["--follow", option], parser)


def test_follow_does_not_treat_keyword_values_as_short_flag_clusters():
    """情境：keyword 文字值包含短旗標字母時不能誤判為批次匯出參數。"""
    parser = build_argument_parser(lambda: "1.0.0")
    cluster_args = parser.parse_args(["--follow", "-ikformat"])
    value_args = parser.parse_args(["--follow", "--keyword=-ikformat"])

    validate_follow_arguments(["--follow", "-ikformat"], parser)
    validate_follow_arguments(["--follow", "--keyword=-ikformat"], parser)

    assert cluster_args.keyword == "format"
    assert value_args.keyword == "-ikformat"


def test_main_routes_follow_before_default_export_and_batch_analysis(
    monkeypatch, capsys, tmp_path
):
    """情境：follow 分流不能產生預設匯出路徑，也不能執行批次分析。"""
    source = tmp_path / "application.log"
    source.write_text("sample\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", str(source), "--follow"])
    monkeypatch.setattr(cli, "_run_cli_follow", lambda args: calls.append(args))
    monkeypatch.setattr(
        cli,
        "resolve_output_path",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不應解析匯出路徑")),
    )
    monkeypatch.setattr(
        cli,
        "_run_cli_analysis",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不應執行批次分析")),
    )

    cli.main()

    assert len(calls) == 1
    assert calls[0].follow is True
    assert calls[0].format == "csv"
    assert capsys.readouterr().out == ""


def test_main_without_follow_keeps_default_csv_batch_flow(monkeypatch, tmp_path):
    """情境：未指定 follow 時仍走既有 CSV 批次分析流程。"""
    source = tmp_path / "application.log"
    source.write_text("sample\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", str(source)])
    monkeypatch.setattr(cli, "_try_run_tui", lambda _args: False)
    monkeypatch.setattr(
        cli,
        "resolve_output_path",
        lambda output, fmt: (calls.append((output, fmt)), "default-report.csv")[1],
    )
    monkeypatch.setattr(cli, "resolve_target_dir", lambda directory: (directory, None))
    monkeypatch.setattr(cli, "_run_cli_analysis", lambda args, _target: calls.append(args))

    cli.main()

    assert calls[0] == (None, "csv")
    assert calls[1].follow is False
    assert calls[1].output == "default-report.csv"


def test_main_rejects_follow_with_explicit_export_option_before_running(
    monkeypatch, capsys, tmp_path
):
    """情境：互斥參數需在追蹤或匯出前失敗並說明原因。"""
    source = tmp_path / "application.log"
    source.write_text("sample\n", encoding="utf-8")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["log-analyzer", str(source), "--follow", "--format=json"],
    )
    monkeypatch.setattr(
        cli,
        "_run_cli_follow",
        lambda _args: (_ for _ in ()).throw(AssertionError("不可啟動追蹤")),
    )

    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("應以非零狀態拒絕互斥參數")

    captured = capsys.readouterr()
    assert "--follow" in captured.err
    assert "--format" in captured.err


def test_main_maps_keyboard_interrupt_to_exit_130_without_traceback(
    monkeypatch, capsys, tmp_path
):
    """情境：追蹤中斷應以 130 結束，不印 traceback。"""
    source = tmp_path / "application.log"
    source.write_text("sample\n", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", str(source), "--follow"])
    monkeypatch.setattr(
        cli,
        "_run_cli_follow",
        lambda _args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_reports_follow_io_error_to_stderr_without_success_output(
    monkeypatch, capsys, tmp_path
):
    """情境：追蹤 I/O 失敗應用非零狀態輸出錯誤，不顯示成功摘要。"""
    source = tmp_path / "application.log"
    source.write_text("sample\n", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", str(source), "--follow"])
    monkeypatch.setattr(
        cli,
        "_run_cli_follow",
        lambda _args: (_ for _ in ()).throw(OSError("reader failed")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "reader failed" in captured.err
    assert captured.out == ""


def test_follow_outputs_initial_and_appended_completed_events_once_then_stops(
    monkeypatch, capsys, tmp_path
):
    """情境：初始及追加完成事件各輸出一次；取消時輸出已讀尾事件並釋放資源。"""
    source = tmp_path / "application.log"
    initial = _log("first") + _log("pending")
    appended = _log("next")
    source.write_bytes(initial)
    args = build_argument_parser(lambda: "1.0.0").parse_args(
        [str(source), "--follow"]
    )
    changed = asyncio.Event()
    sessions = []
    runners = []
    delivered = []
    real_session = follow.LiveSession

    def capture_session(conditions):
        session = real_session(conditions, directory=tmp_path)
        sessions.append(session)
        return session

    class CapturingRunner(LiveRunner):
        def __init__(self, session, *, on_events=None, on_update=None):
            def capture_events(events):
                delivered.extend(events)
                if on_events is not None:
                    on_events(events)
                changed.set()

            def capture_update():
                if on_update is not None:
                    on_update()
                changed.set()

            super().__init__(session, on_events=capture_events, on_update=capture_update)
            runners.append(self)

    monkeypatch.setattr(follow, "LiveSession", capture_session)
    monkeypatch.setattr(follow, "LiveRunner", CapturingRunner)

    async def run():
        follow_task = asyncio.create_task(follow.run_follow(args))
        try:
            await _wait_for_condition(
                changed,
                lambda: bool(sessions)
                and sessions[0].offset == len(initial)
                and bool(delivered),
                "初始內容讀取並交付完成事件",
            )
            assert [event["message"] for event in delivered] == ["first"]
            assert sessions[0].has_pending

            first_output = capsys.readouterr()
            assert first_output.out == initial.splitlines(keepends=True)[0].decode()
            assert "尚未完成" in first_output.err

            with source.open("ab") as stream:
                stream.write(appended)
            expected_offset = len(initial) + len(appended)
            await _wait_for_condition(
                changed,
                lambda: sessions[0].offset == expected_offset and len(delivered) >= 2,
                "追加內容讀取並完成前一事件",
            )
            assert [event["message"] for event in delivered] == ["first", "pending"]

            follow_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await follow_task

            final_output = capsys.readouterr()
            assert final_output.out == (
                initial.splitlines(keepends=True)[1].decode() + appended.decode()
            )
            assert final_output.out.count("pending") == 1
            assert final_output.out.count("next") == 1
            assert not runners[0].running
            assert sessions[0]._closed
            assert [event["message"] for event in delivered] == [
                "first",
                "pending",
                "next",
            ]
        finally:
            if not follow_task.done():
                follow_task.cancel()
                with suppress(asyncio.CancelledError):
                    await follow_task

    asyncio.run(run())


def test_follow_announces_partial_bytes_using_has_pending(monkeypatch, capsys, tmp_path):
    """情境：沒有完整事件物件的部分位元組也要提示仍有內容待完成。"""
    source = tmp_path / "application.log"
    partial = b"partial log line"
    source.write_bytes(partial)
    args = build_argument_parser(lambda: "1.0.0").parse_args(
        [str(source), "--follow"]
    )
    changed = asyncio.Event()
    sessions = []
    real_session = follow.LiveSession

    def capture_session(conditions):
        session = real_session(conditions, directory=tmp_path)
        sessions.append(session)
        return session

    class NotifyingRunner(LiveRunner):
        def __init__(self, session, *, on_events=None, on_update=None):
            def notify_update():
                if on_update is not None:
                    on_update()
                changed.set()

            super().__init__(session, on_events=on_events, on_update=notify_update)

    monkeypatch.setattr(follow, "LiveSession", capture_session)
    monkeypatch.setattr(follow, "LiveRunner", NotifyingRunner)

    async def run():
        follow_task = asyncio.create_task(follow.run_follow(args))
        try:
            await _wait_for_condition(
                changed,
                lambda: bool(sessions)
                and sessions[0].offset == len(partial)
                and sessions[0].has_pending,
                "部分位元組讀取並反映待完成狀態",
            )
            assert sessions[0].pending is None
            output = capsys.readouterr()
            assert output.out == ""
            assert "尚未完成" in output.err

            follow_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await follow_task
            assert sessions[0]._closed
        finally:
            if not follow_task.done():
                follow_task.cancel()
                with suppress(asyncio.CancelledError):
                    await follow_task

    asyncio.run(run())


def test_follow_final_flush_failure_is_not_hidden_by_keyboard_cancellation(
    monkeypatch, tmp_path
):
    """情境：Ctrl+C 清理時的磁碟錯誤須取代 130，不能被當成正常中斷。"""
    source = tmp_path / "application.log"
    source.write_bytes(_log("first"))
    args = build_argument_parser(lambda: "1.0.0").parse_args(
        [str(source), "--follow"]
    )
    started = asyncio.Event()
    sessions = []
    real_session = follow.LiveSession

    def capture_session(conditions):
        session = real_session(conditions, directory=tmp_path)
        sessions.append(session)
        return session

    class StopFailingRunner:
        def __init__(self, session, *, on_events=None, on_update=None):
            self.running = False
            self.failure = None

        async def start(self, conditions=None):
            self.running = True
            started.set()

        async def stop(self):
            self.running = False
            raise OSError("disk full while saving final event")

    monkeypatch.setattr(follow, "LiveSession", capture_session)
    monkeypatch.setattr(follow, "LiveRunner", StopFailingRunner)

    async def run():
        follow_task = asyncio.create_task(follow.run_follow(args))
        await asyncio.wait_for(started.wait(), timeout=6)
        follow_task.cancel()
        with pytest.raises(OSError, match="disk full while saving final event"):
            await follow_task
        assert sessions[0]._closed

    asyncio.run(run())


def test_follow_reports_each_new_gap_and_source_recovery_once(capsys):
    """情境：新缺口與中斷恢復只向 stderr 呈現一次，stdout 保持純事件資料。"""
    session = SimpleNamespace(has_pending=False, state="tracking", gaps=[])
    output = follow._FollowOutput(session, asyncio.Event())

    output.on_update()
    session.gaps.append("偵測到來源截斷，可能遺失未讀內容")
    session.state = "interrupted"
    output.on_update()
    output.on_update()
    session.gaps.append("來源暫時無法存取")
    session.state = "tracking"
    output.on_update()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("偵測到來源截斷") == 1
    assert captured.err.count("來源暫時無法存取") == 1
    assert captured.err.count("等待恢復") == 1
    assert captured.err.count("已恢復") == 1


def test_follow_natural_runner_io_error_reports_and_closes_without_export(
    monkeypatch, capsys, tmp_path
):
    """情境：Runner I/O 失敗需自然結束、送錯誤至 stderr 並關閉 session。"""
    source = tmp_path / "application.log"
    source.write_bytes(_log("pending"))
    monkeypatch.setattr(
        cli.sys, "argv", ["log-analyzer", str(source), "--follow"]
    )
    sessions = []
    real_session = follow.LiveSession

    def capture_session(conditions):
        session = real_session(conditions, directory=tmp_path)
        sessions.append(session)
        return session

    async def failing_watcher(_path, _stop_event):
        raise OSError("watch source failed")
        yield None

    monkeypatch.setattr(follow, "LiveSession", capture_session)
    monkeypatch.setattr(
        "log_analyzer.application.live.runner.watch_source", failing_watcher
    )
    original_asyncio_run = asyncio.run

    def bounded_asyncio_run(coroutine):
        return original_asyncio_run(asyncio.wait_for(coroutine, timeout=6))

    monkeypatch.setattr(asyncio, "run", bounded_asyncio_run)
    monkeypatch.setattr(
        cli,
        "resolve_output_path",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不應建立匯出路徑")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert sessions[0]._closed
    captured = capsys.readouterr()
    assert "watch source failed" in captured.err
    assert "持續追蹤中" in captured.err
    assert captured.out == ""
