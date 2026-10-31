"""輸入等待須核對目標欄位，不以另一個同值欄位提早成功。"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "portable"))
from terminal_session import TerminalSession


def _row(value, *, focused):
    text = " ▊ " + value.ljust(24) + " ▎ "
    color = "0087d7" if focused else "191919"
    return [SimpleNamespace(data=char, fg=color, bg="121212") for char in text]


def test_duplicate_date_in_unfocused_field_is_not_the_target_input():
    """開始日期已填好，不能冒充仍為空白的結束日期完成。"""
    terminal = object.__new__(TerminalSession)
    rows = [_row("2026-10-24", focused=False), _row("YYYY-MM-DD", focused=True)]
    terminal.screen = SimpleNamespace(buffer=rows, lines=2, columns=len(rows[0]))
    assert terminal._focused_input_content(1) == "YYYY-MM-DD"
    assert terminal._focused_input_content(1, "2026-10-24") is None
    rows[1] = _row("2026-10-24", focused=True)
    assert terminal._focused_input_content(1, "2026-10-24") == "2026-10-24"


def test_input_completion_does_not_accept_an_unfocused_value():
    """焦點尚未到欄位時，既有文字不能被當作已取得焦點。"""
    terminal = object.__new__(TerminalSession)
    rows = [_row("80", focused=False)]
    terminal.screen = SimpleNamespace(buffer=rows, lines=1, columns=len(rows[0]))
    assert terminal._focused_input_content(1, "80") is None
    rows[0] = _row("80", focused=True)
    assert terminal._focused_input_content(1, "80") == "80"


def test_tall_input_border_is_not_mistaken_for_its_text_row():
    """三列高的輸入框須讀取正文，不能把上邊框當成輸入值。"""
    terminal = object.__new__(TerminalSession)
    rows = [_row("▔" * 24, focused=True), _row("100", focused=True)]
    terminal.screen = SimpleNamespace(buffer=rows, lines=2, columns=len(rows[0]))
    assert terminal._focused_input_content(1) == "100"


def test_same_text_placeholder_is_empty_but_real_input_is_not():
    """門檻清空後仍顯示提示 80，須用文字樣式區分既有值。"""
    terminal = object.__new__(TerminalSession)
    rows = [_row("80", focused=True)]
    terminal.screen = SimpleNamespace(buffer=rows, lines=1, columns=len(rows[0]))
    for cell in rows[0]:
        if cell.data in "80":
            cell.fg = "e0e0e0"
    assert not terminal._focused_input_is_empty(1)
    for cell in rows[0]:
        if cell.data in "80":
            cell.fg = "797979"
    assert terminal._focused_input_is_empty(1)
