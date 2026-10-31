"""以真實可攜執行檔驗證即時追蹤的讀取、暫停、重建與結束流程。"""

from __future__ import annotations

from pathlib import Path

from terminal_session import TerminalSession

_LIVE_LABEL_WIDTH = 4
_LIVE_SWITCH_LEFT_BORDER = "▊"


def _entry(index: int, marker: str) -> str:
    return (
        f"2026-10-31 10:00:{index:02d}.000 [worker-{index}] "
        f"ERROR example.Live - {marker}\n"
    )


def _compact_text(terminal: TerminalSession) -> str:
    return "".join(terminal.text().split())


def _status_matches(  # noqa: PLR0913 - keep each observable status counter explicit
    terminal: TerminalSession,
    *,
    state: str,
    completed: int,
    matched: int,
    received_bytes: int,
    pending: bool,
    backlog: int = 0,
) -> bool:
    text = _compact_text(terminal)
    pending_text = "有" if pending else "無"
    return (
        f"狀態：{state}｜完成{completed}｜命中{matched}" in text
        and f"接收{received_bytes}B｜待讀{backlog}B｜待處理：{pending_text}" in text
    )


def _wait_status(  # noqa: PLR0913 - keep each observable status counter explicit
    terminal: TerminalSession,
    *,
    state: str,
    completed: int,
    matched: int,
    received_bytes: int,
    pending: bool,
    description: str,
    backlog: int = 0,
) -> None:
    terminal.wait(
        lambda: _status_matches(
            terminal,
            state=state,
            completed=completed,
            matched=matched,
            received_bytes=received_bytes,
            pending=pending,
            backlog=backlog,
        ),
        description,
    )


def _row_text_columns(terminal: TerminalSession, row: int, text: str) -> list[int]:
    """從 pyte cell buffer 找出同列文字在終端中的實際欄位。

    Args:
        terminal: 正在讀取畫面的 PTY 終端工作階段。
        row: 要檢查的零起始終端列。
        text: 要尋找的連續文字。

    Returns:
        list[int]: 每次命中的實際 cell 起始欄位，保留畫面上的出現順序。
    """
    characters: list[str] = []
    columns: list[int] = []
    cells = terminal.screen.buffer[row]
    for column in range(terminal.screen.columns):
        cell_data = cells[column].data
        if not cell_data:
            continue
        for character in cell_data:
            characters.append(character)
            columns.append(column)

    rendered = "".join(characters)
    found_columns: list[int] = []
    offset = 0
    while (match_index := rendered.find(text, offset)) >= 0:
        found_columns.append(columns[match_index])
        offset = match_index + 1
    return found_columns


def _live_switch_point(terminal: TerminalSession) -> tuple[int, int] | None:
    """以同列重建按鈕錨點和實際 Switch 左緣定位追蹤控制。"""
    screen = terminal.screen
    for row in range(screen.lines):
        anchors = _row_text_columns(terminal, row, "重新分析來源")
        if not anchors:
            continue
        labels = _row_text_columns(terminal, row, "追蹤")
        cells = screen.buffer[row]
        for label_column in labels:
            switch_column = label_column + _LIVE_LABEL_WIDTH
            if (
                switch_column < screen.columns
                and any(anchor_column > switch_column for anchor_column in anchors)
                and cells[switch_column].data == _LIVE_SWITCH_LEFT_BORDER
            ):
                return switch_column, row
    return None


def _similarity_checkbox_point(terminal: TerminalSession, *, focused=False) -> tuple[int, int] | None:
    """以實際 checkbox 標記定位相似訊息控制，而非點擊文字中心。"""
    screen = terminal.screen
    for row in range(screen.lines):
        labels = _row_text_columns(terminal, row, "相似訊息")
        if not labels:
            continue
        label_column = labels[0]
        cells = screen.buffer[row]
        if focused:
            borders = [index for index in range(label_column) if cells[index].data == "▊"]
            if not borders or not terminal._focused_border(cells[borders[-1]]):
                continue
        for column in range(max(0, label_column - 5), label_column):
            if (
                cells[column].data == "▐"
                and column + 2 < screen.columns
                and cells[column + 2].data == "▌"
            ):
                return column + 1, row
    return None


def _similarity_checked(terminal):
    point = _similarity_checkbox_point(terminal)
    if point is None:
        return False
    column, row = point
    cell = terminal.screen.buffer[row][column]
    if len(cell.fg) != 6 or len(cell.bg) != 6:
        return False
    def brightness(color):
        return sum(int(color[index:index + 2], 16) for index in (0, 2, 4))
    return brightness(cell.fg) > brightness(cell.bg) + 80


def _toggle_live_switch(terminal: TerminalSession) -> None:
    terminal.wait(
        lambda: _live_switch_point(terminal) is not None,
        "Live tracking switch did not become visible",
    )
    point = _live_switch_point(terminal)
    assert point is not None
    column, row = point
    terminal.click_at(column, row)


def _select_live_mode(terminal: TerminalSession) -> None:
    terminal.wait_text("批次分析")
    terminal.send("\r")
    terminal.wait_text("Dismiss menu")
    terminal.send("\x1b[B\r")
    terminal.wait(
        lambda: _live_switch_point(terminal) is not None,
        "Live mode did not show its tracking controls",
    )


def verify_live_tui(target, root: Path) -> list[str]:  # noqa: PLR0915 - keep the PTY lifecycle assertions ordered
    """用可攜執行檔操作真實終端，核對追蹤生命週期與事件計數。

    驗證單一來源檔的初始讀取、暫停期間來源位移不變、接續後補讀、
    重新分析成功並保持暫停，以及結束時只交付一次尚未完成的尾端事件。
    本函式不匯入應用程式，也不要求目標環境安裝 Python。

    Args:
        target: 可攜版驗證器建立的隔離目標與終端啟動器。
        root: 本次驗證的 evidence 根目錄。

    Returns:
        list[str]: 本次實際完成的即時追蹤驗證項目。

    Raises:
        AssertionError: 終端操作未完成、事件或位移計數不符，或尾端事件重複。
    """
    directory = target.data / "work A" / "live-tui"
    directory.mkdir()
    source = directory / "tracking.log"
    source.write_text(
        _entry(1, "portable-live-initial-1")
        + _entry(2, "portable-live-initial-2"),
        encoding="utf-8",
    )
    initial_size = source.stat().st_size
    command = target.command([target.executable], terminal=True)
    evidence = root / "tui-live"

    with TerminalSession(
        command,
        target.data / "work A",
        target.environment,
        evidence,
        dimensions=(45, 160),
    ) as terminal:
        terminal.wait_text("開始分析")
        terminal.fill_source_input(target.path("work A/live-tui/tracking.log"))
        terminal.wait_text("單一檔案")

        # 路徑欄位緊接在模式 Select 之後，Shift+Tab 將焦點交回模式欄。
        terminal.send("\x1b[Z")
        _select_live_mode(terminal)
        _toggle_live_switch(terminal)

        terminal.wait_text("portable-live-initial-1")
        _wait_status(
            terminal,
            state="追蹤中",
            completed=1,
            matched=1,
            received_bytes=initial_size,
            pending=True,
            description="Initial live read did not retain exactly one pending tail",
        )
        assert "portable-live-initial-2" not in terminal.text()
        terminal.capture("initial-one-complete-one-pending")

        _toggle_live_switch(terminal)
        _wait_status(
            terminal,
            state="已暫停",
            completed=1,
            matched=1,
            received_bytes=initial_size,
            pending=True,
            description="Live reader did not pause at the initial byte offset",
        )
        paused_offset = initial_size
        with source.open("a", encoding="utf-8") as stream:
            stream.write(
                _entry(3, "portable-live-paused-3")
                + _entry(4, "portable-live-paused-4")
            )
        appended_size = source.stat().st_size
        assert appended_size > paused_offset

        # 切換跟隨選項會同步刷新面板，讓暫停後的計數可被觀察。
        terminal.click("跟隨最新")
        terminal.wait_text("新增：0 筆")
        _wait_status(
            terminal,
            state="已暫停",
            completed=1,
            matched=1,
            received_bytes=paused_offset,
            pending=True,
            description="Paused tracking consumed bytes appended while paused",
            backlog=appended_size - paused_offset,
        )
        assert "portable-live-paused-3" not in terminal.text()
        assert "portable-live-paused-4" not in terminal.text()
        terminal.capture("paused-source-grew-reader-did-not")

        _toggle_live_switch(terminal)
        _wait_status(
            terminal,
            state="追蹤中",
            completed=3,
            matched=3,
            received_bytes=appended_size,
            pending=True,
            description="Resume did not catch up exactly to the appended byte offset",
        )
        terminal.wait_text("portable-live-initial-2")
        terminal.wait_text("portable-live-paused-3")
        assert "portable-live-paused-4" not in terminal.text()
        terminal.capture("resume-caught-up-once")

        _toggle_live_switch(terminal)
        _wait_status(
            terminal,
            state="已暫停",
            completed=3,
            matched=3,
            received_bytes=appended_size,
            pending=True,
            description="Live reader did not pause before rebuilding the source",
        )
        terminal.click("重新分析來源")
        terminal.wait(
            lambda: "新追蹤保持暫停" in _compact_text(terminal)
            and _status_matches(
                terminal,
                state="已暫停",
                completed=3,
                matched=3,
                received_bytes=appended_size,
                pending=True,
            ),
            "Successful source rebuild did not publish a paused session",
        )
        assert "portable-live-paused-3" in terminal.text()
        assert "portable-live-paused-4" not in terminal.text()
        terminal.capture("rebuild-success-paused")

        _toggle_live_switch(terminal)
        _wait_status(
            terminal,
            state="追蹤中",
            completed=3,
            matched=3,
            received_bytes=appended_size,
            pending=True,
            description="Rebuilt session did not resume from its captured cutoff",
        )
        with source.open("a", encoding="utf-8") as stream:
            stream.write(_entry(5, "portable-live-final-tail"))
        final_size = source.stat().st_size
        _wait_status(
            terminal,
            state="追蹤中",
            completed=4,
            matched=4,
            received_bytes=final_size,
            pending=True,
            description="Final appended line was not read as a pending tail",
        )
        assert "portable-live-final-tail" not in terminal.text()
        terminal.capture("before-end-final-tail-pending")

        terminal.click("結束追蹤")
        terminal.wait_text("portable-live-final-tail")
        _wait_status(
            terminal,
            state="已結束",
            completed=5,
            matched=5,
            received_bytes=final_size,
            pending=False,
            description="Ending tracking did not deliver and finalize the tail once",
        )
        assert terminal.text().count("portable-live-final-tail") == 1
        for marker in (
            "portable-live-initial-1",
            "portable-live-initial-2",
            "portable-live-paused-3",
            "portable-live-paused-4",
            "portable-live-final-tail",
        ):
            assert terminal.text().count(marker) == 1, f"Missing or duplicate event: {marker}"
        terminal.capture("ended-final-tail-once")

    assert set(directory.iterdir()) == {source}, "TUI tracking created an unexpected source file"
    return [
        "tui-live-single-file-initial-read-and-pending-tail",
        "tui-live-paused-without-consuming-appends",
        "tui-live-resume-catches-up-once",
        "tui-live-rebuild-success-leaves-session-paused",
        "tui-live-stop-finalizes-tail-once",
    ]


def _similarity_entry(index: int, level: str, message: str) -> str:
    return (
        f"2026-10-31 10:00:{index:02d}.000 [worker-{index}] "
        f"{level} example.Similarity{index} - {message}\n"
    )


def _fill_labeled_input(
    terminal: TerminalSession,
    label: str,
    current_value: str,
    new_value: str,
) -> None:
    """依欄位標籤與相鄰輸入列更新表單，避免命中右側事件文字。"""
    result: list[tuple[int, int]] = []

    def locate() -> bool:
        result.clear()
        screen = terminal.screen
        for label_row in range(screen.lines):
            label_columns = _row_text_columns(terminal, label_row, label)
            if not label_columns:
                continue
            for input_row in range(label_row + 1, min(screen.lines, label_row + 7)):
                columns = _row_text_columns(terminal, input_row, current_value)
                cells = screen.buffer[input_row]
                for column in columns:
                    left = next(
                        (
                            index
                            for index in range(column - 1, -1, -1)
                            if cells[index].data == "▊"
                        ),
                        None,
                    )
                    right = next(
                        (
                            index
                            for index in range(column + 1, screen.columns)
                            if cells[index].data == "▎"
                        ),
                        None,
                    )
                    if (
                        left is not None
                        and right is not None
                        and label_columns[0] <= left <= label_columns[0] + 8
                    ):
                        result.append((column, input_row))
                        return True
        return False

    terminal.wait(locate, f"Input for {label!r} did not become visible")
    column, row = result[0]
    terminal.fill_input_at(column, row, new_value)


def verify_live_similarity_tui(  # noqa: PLR0915 - preserve ordered terminal assertions
    target, root: Path
) -> list[str]:
    """用可攜執行檔核對即時關鍵字、Level 與相似條件交集。"""
    directory = target.data / "work A" / "live-tui-similarity"
    directory.mkdir()
    source = directory / "tracking.log"
    reference = "needle Payment declined"
    changed_reference = "needle Connection reset"
    source.write_text(
        _similarity_entry(1, "ERROR", reference)
        + _similarity_entry(2, "ERROR", "needle payment declined")
        + _similarity_entry(3, "DEBUG", reference)
        + _similarity_entry(4, "ERROR", changed_reference)
        + _similarity_entry(5, "ERROR", changed_reference),
        encoding="utf-8",
    )
    initial_size = source.stat().st_size
    command = target.command([target.executable], terminal=True)
    evidence = root / "tui-live-similarity"

    with TerminalSession(
        command,
        target.data / "work A",
        target.environment,
        evidence,
        dimensions=(45, 160),
    ) as terminal:
        terminal.wait_text("開始分析")
        terminal.fill_source_input(target.path("work A/live-tui-similarity/tracking.log"))
        terminal.wait_text("單一檔案")
        terminal.send("\x1b[Z")
        _select_live_mode(terminal)

        terminal.fill_input("例如：Order_123 或 SQLException", "needle")
        # 關鍵字之後依序為清除、相似訊息；以真實焦點導航捲到表單下方。
        terminal.send("\t\t")
        terminal.wait(
            lambda: _similarity_checkbox_point(terminal, focused=True) is not None,
            "Similarity checkbox did not receive focus",
        )
        terminal.send(" ")
        terminal.wait(lambda: _similarity_checked(terminal), "Similarity checkbox did not become checked")
        terminal.send("\t")
        terminal.wait_text("輸入要比對的訊息正文")
        terminal.fill_focused_input("輸入要比對的訊息正文", reference, max_column=80)
        terminal.send("\t")
        terminal.fill_focused_input("80", "100", max_column=80)
        terminal.capture("keyword-level-similarity-conditions")

        _toggle_live_switch(terminal)
        _wait_status(
            terminal,
            state="追蹤中",
            completed=4,
            matched=1,
            received_bytes=initial_size,
            pending=True,
            description="Initial AND-filtered read did not retain its expected match and pending tail",
        )
        initial_view = terminal.text()
        assert "g0:L1" in initial_view
        assert "g0:L2" not in initial_view, "Case-different similarity passed threshold 100"
        assert "g0:L3" not in initial_view, "DEBUG event passed the default selected Level set"
        assert "g0:L4" not in initial_view
        terminal.capture("initial-case-level-similarity-intersection")

        _toggle_live_switch(terminal)
        _wait_status(
            terminal,
            state="已暫停",
            completed=4,
            matched=1,
            received_bytes=initial_size,
            pending=True,
            description="Similarity tracking did not pause at the initial source offset",
        )
        with source.open("a", encoding="utf-8") as stream:
            stream.write(_similarity_entry(6, "ERROR", reference))
        appended_size = source.stat().st_size
        assert appended_size > initial_size
        terminal.click("跟隨最新")
        terminal.wait_text("新增：0 筆")
        _wait_status(
            terminal,
            state="已暫停",
            completed=4,
            matched=1,
            received_bytes=initial_size,
            pending=True,
            description="Paused similarity tracking consumed the appended event",
            backlog=appended_size - initial_size,
        )
        assert "g0:L6" not in terminal.text()
        terminal.capture("paused-before-reference-change")

        _fill_labeled_input(terminal, "參考訊息", reference, changed_reference)
        assert "100" in terminal.text(), "Similarity threshold changed unexpectedly"
        # 條件提示在可捲動表單頂端，改值後以真實滾輪返回提示位置。
        point = terminal.find(changed_reference, max_column=80)
        assert point is not None
        column, row = point
        terminal.send(f"\x1b[<64;{column + 1};{row + 1}M" * 20)
        terminal.wait_text("相似訊息條件已變更")
        _toggle_live_switch(terminal)
        terminal.wait_text("條件已變更；請使用")
        _wait_status(
            terminal,
            state="已暫停",
            completed=4,
            matched=1,
            received_bytes=initial_size,
            pending=True,
            description="Changed reference incorrectly resumed the existing session",
            backlog=appended_size - initial_size,
        )
        terminal.capture("changed-reference-resume-refused")

        terminal.click("重新分析來源")
        terminal.wait(
            lambda: "新追蹤保持暫停" in _compact_text(terminal)
            and _status_matches(
                terminal,
                state="已暫停",
                completed=5,
                matched=2,
                received_bytes=appended_size,
                pending=True,
            )
            and "g0:L4" in terminal.text()
            and "g0:L5" in terminal.text(),
            "Rebuild did not apply the changed reference while keeping tracking paused",
        )
        rebuilt_view = terminal.text()
        assert "g0:L4" in rebuilt_view and "g0:L5" in rebuilt_view
        assert "g0:L1" not in rebuilt_view
        terminal.capture("rebuild-applied-new-reference-paused")

        _toggle_live_switch(terminal)
        _wait_status(
            terminal,
            state="追蹤中",
            completed=5,
            matched=2,
            received_bytes=appended_size,
            pending=True,
            description="Rebuilt similarity session did not resume from its captured cutoff",
        )
        with source.open("a", encoding="utf-8") as stream:
            stream.write(_similarity_entry(7, "ERROR", changed_reference))
        final_size = source.stat().st_size
        _wait_status(
            terminal,
            state="追蹤中",
            completed=6,
            matched=2,
            received_bytes=final_size,
            pending=True,
            description="Appended similarity event was duplicated or scored before completion",
        )
        assert "g0:L7" not in terminal.text()
        terminal.capture("appended-event-pending-before-stop")

        terminal.click("結束追蹤")
        _wait_status(
            terminal,
            state="已結束",
            completed=7,
            matched=3,
            received_bytes=final_size,
            pending=False,
            description="Ending tracking did not finalize the matching tail exactly once",
        )
        final_view = terminal.text()
        for line_number in (4, 5, 7):
            assert final_view.count(f"g0:L{line_number}") == 1, (
                f"Matching event at line {line_number} was missing or duplicated"
            )
        for line_number in (1, 2, 3, 6):
            assert f"g0:L{line_number}" not in final_view
        terminal.capture("ended-tail-once-new-reference")

    assert set(directory.iterdir()) == {source}, "Similarity TUI created an unexpected source file"
    return [
        "tui-live-similarity-keyword-level-and-reference-threshold-100",
        "tui-live-similarity-case-sensitive-match",
        "tui-live-similarity-pause-and-reject-changed-reference-resume",
        "tui-live-similarity-rebuild-applies-new-reference",
        "tui-live-similarity-appended-events-and-final-tail-once",
    ]
