"""以實際可攜執行檔驗證追加讀取與 Ctrl+C 的尾端事件交付。"""

from __future__ import annotations

from pathlib import Path
import os

from terminal_session import TerminalSession


def verify_live_cli(target, root: Path) -> list[str]:
    """在既有乾淨執行環境驗證 follow，不匯入應用程式或使用測試替身。

    Args:
        target: 可攜版驗證器已解壓並隔離的執行環境。
        root: 本次驗證的證據目錄。

    Returns:
        list[str]: 本次實際完成的驗證項目。

    Raises:
        AssertionError: 初始／追加事件未交付、重複交付、退出碼不符或產生報表。
    """
    directory = target.data / "work A" / "live-cli"
    directory.mkdir()
    source = directory / "follow.log"
    existing_reports = set((target.data / "work A").glob("*.csv"))

    def entry(index: int) -> str:
        return (
            f"2026-10-31 10:00:0{index}.000 [main] ERROR Service - "
            f"portable-follow-event-{index}\n"
        )

    source.write_text(entry(1) + entry(2), encoding="utf-8")
    command = target.command(
        [target.executable, target.path("work A/live-cli/follow.log"),
         "--follow", "--level", "ALL"],
        terminal=True,
    )
    evidence = root / "cli-follow"
    with TerminalSession(
        command, target.data / "work A", target.environment, evidence,
    ) as terminal:
        terminal.wait_text("portable-follow-event-1")
        terminal.wait_text("尚未完成")
        assert "portable-follow-event-2" not in terminal.text()
        with source.open("a", encoding="utf-8") as stream:
            stream.write(entry(3) + entry(4))
        terminal.wait_text("portable-follow-event-3")
        assert "portable-follow-event-4" not in terminal.text()
        terminal.capture("appended-before-stop")
        terminal.send("\x03")
        assert terminal.finished.wait(15), "Portable follow did not stop after Ctrl+C"
        assert terminal.reader_error is None, terminal.reader_error
        if os.name == "nt":
            exit_code = terminal.process.wait()
        else:
            _, status = os.waitpid(terminal.pid, 0)
            exit_code = os.waitstatus_to_exitcode(status)
        assert exit_code == 130, f"Portable follow exited with {exit_code}"
        terminal.capture("stopped")
        transcript = (evidence / "terminal.ansi").read_text(encoding="utf-8")
        for index in range(1, 5):
            marker = f"portable-follow-event-{index}"
            assert transcript.count(marker) == 1, f"Missing or duplicate event: {marker}"
        assert "Traceback" not in transcript

    assert set(directory.iterdir()) == {source}, "Follow created an unexpected report"
    assert set((target.data / "work A").glob("*.csv")) == existing_reports, "Follow created a default CSV"
    return ["cli-follow-initial-and-append-once", "cli-follow-ctrl-c-tail-once-exit-130"]


def _similarity_entry(index: int, level: str, message: str) -> str:
    return (
        f"2026-10-31 10:00:{index:02d}.000 [worker-{index}] "
        f"{level} example.PortableSimilarity{index} - {message}\n"
    )


def verify_live_similarity_cli(target, root: Path) -> list[str]:
    """用真實可攜執行檔核對 CLI 的即時相似條件與完成事件輸出。

    Args:
        target: 可攜版驗證器建立的隔離目標與執行環境。
        root: 本次驗證的證據目錄。

    Returns:
        list[str]: 本次實際完成的相似追蹤驗證項目。

    Raises:
        AssertionError: 相似、關鍵字或 Level 條件不符，事件重複／遺失，
            pending 提示消失，或 Ctrl+C 未完成清理。
    """
    directory = target.data / "work A" / "live-cli-similarity"
    directory.mkdir()
    source = directory / "follow.log"
    reference = "needle Payment declined"
    source.write_text(
        _similarity_entry(1, "ERROR", reference)
        + _similarity_entry(2, "ERROR", "needle payment declined")
        + _similarity_entry(3, "WARN", reference)
        + _similarity_entry(4, "ERROR", "needle Connection reset")
        + _similarity_entry(5, "ERROR", reference),
        encoding="utf-8",
    )
    existing_reports = set((target.data / "work A").glob("*.csv"))
    command = target.command(
        [
            target.executable,
            target.path("work A/live-cli-similarity/follow.log"),
            "--follow",
            "--level",
            "ERROR",
            "--keyword",
            "needle",
            "--similar-to",
            reference,
            "--similarity-threshold",
            "100",
        ],
        terminal=True,
    )
    evidence = root / "cli-live-similarity"

    with TerminalSession(
        command,
        target.data / "work A",
        target.environment,
        evidence,
        dimensions=(45, 160),
    ) as terminal:
        terminal.wait_text("PortableSimilarity1")
        terminal.wait_text("有尚未完成的日誌內容")
        initial_view = terminal.text()
        for index in (2, 3, 4, 5):
            assert f"PortableSimilarity{index}" not in initial_view
        terminal.capture("initial-exact-case-level-match-and-pending")

        with source.open("a", encoding="utf-8") as stream:
            stream.write(
                _similarity_entry(6, "ERROR", reference)
                + _similarity_entry(7, "ERROR", reference)
            )
        terminal.wait_text("PortableSimilarity5")
        terminal.wait_text("PortableSimilarity6")
        appended_view = terminal.text()
        assert "PortableSimilarity7" not in appended_view
        for index in (2, 3, 4):
            assert f"PortableSimilarity{index}" not in appended_view
        terminal.capture("appended-complete-events-before-tail")

        terminal.send("\x03")
        assert terminal.finished.wait(15), "Portable similarity follow did not stop after Ctrl+C"
        assert terminal.reader_error is None, terminal.reader_error
        if os.name == "nt":
            exit_code = terminal.process.wait()
        else:
            _, status = os.waitpid(terminal.pid, 0)
            exit_code = os.waitstatus_to_exitcode(status)
        assert exit_code == 130, f"Portable similarity follow exited with {exit_code}"
        terminal.capture("stopped-final-pending-event")
        transcript = (evidence / "terminal.ansi").read_text(encoding="utf-8")
        for index in (1, 5, 6, 7):
            marker = f"PortableSimilarity{index}"
            assert transcript.count(marker) == 1, f"Missing or duplicate matching event: {marker}"
        for index in (2, 3, 4):
            marker = f"PortableSimilarity{index}"
            assert marker not in transcript, f"Filtered event was output: {marker}"
        assert "Traceback" not in transcript

    assert set(directory.iterdir()) == {source}, "Similarity follow created an unexpected report"
    assert set((target.data / "work A").glob("*.csv")) == existing_reports, (
        "Similarity follow created a default CSV"
    )
    return [
        "cli-live-similarity-keyword-level-and-reference-threshold-100",
        "cli-live-similarity-case-sensitive-filter",
        "cli-live-similarity-initial-and-appended-events-once",
        "cli-live-similarity-ctrl-c-finalizes-tail-once-exit-130",
    ]
