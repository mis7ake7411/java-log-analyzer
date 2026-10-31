"""Log entry assembly and continuation classification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from log_analyzer.domain.log_types import LogEntry, ScanDiagnostics
from log_analyzer.domain.logback_mdc import extract_mdc

from ..timezone import to_local_timezone

_STACKTRACE_HEADER_RE = re.compile(r"^[A-Za-z_][\w.$]*(?:Exception|Error)(?::|\s|$)")

def iter_entries(scanned_lines, accumulator):
    """將掃描器產生的實體行組合成完整事件。

    Args:
        scanned_lines: 含行號、解碼文字與首行匹配結果的 iterator。
        accumulator: 累積目前事件與續行的事件累積器。

    Returns:
        Iterator[LogEntry]: 依來源順序產生已完成的事件。

    Raises:
        IndexError: 首行匹配缺少事件建立所需的具名群組。
        OSError: 讀取來源行時發生檔案錯誤。
    """
    for line_num, line, match in scanned_lines:
        if match:
            completed_entry = accumulator.start_entry(match, line_num)
            if completed_entry:
                yield completed_entry
            continue

        accumulator.append_continuation(line_num, line)

    completed_entry = accumulator.finish()
    if completed_entry:
        yield completed_entry


@dataclass(slots=True)
class _EntryAccumulator:
    """保存單一檔案尚未完成的事件，將首行與後續堆疊組合起來。

    dataclass 產生初始化方法；slots=True 限定可用欄位。
    current_entry=None 可能代表尚無事件，或目前事件被時間條件排除。
    """

    filename: str
    start_time: datetime | None
    end_time: datetime | None
    current_entry: LogEntry | None = None
    diagnostics: ScanDiagnostics | None = None

    def start_entry(self, match, line_num):
        """交出上一筆事件，再用新的正則匹配建立目前事件並套用時間篩選。

        Args:
            match: 包含 timestamp、thread、level、logger、message 的正則匹配。
            line_num: 原始日誌從 1 起算的實體行號。

        Returns:
            LogEntry | None: 上一筆已累積的事件；目前事件另行初始化。

        Raises:
            IndexError: 首行正則缺少必要的具名群組。
        """
        completed_entry = self.current_entry
        self.current_entry = _create_entry(match, self.filename, line_num)
        if self.current_entry and self.diagnostics is not None:
            self.diagnostics.parsed_entries += 1
        if self.current_entry and not _is_entry_in_time_range(
            self.current_entry,
            self.start_time,
            self.end_time,
        ):
            self.current_entry = None
        elif self.current_entry and self.diagnostics is not None:
            self.diagnostics.after_time += 1
        return completed_entry

    def append_continuation(self, line_num, line):
        """有保留中的事件才附加續行，避免被時間排除的事件重新混入結果。

        Args:
            line_num: 原始日誌從 1 起算的實體行號。
            line: 目前讀到的原文行，可能保留換行。

        Returns:
            None: 有目前事件時才原地附加續行。
        """
        if self.current_entry:
            _append_continuation_line(self.current_entry, line_num, line)

    def finish(self):
        """在檔尾交出目前事件；只讀取狀態，不會自動清空 current_entry。
        Returns:
            LogEntry | None: 目前累積的事件，不會清空狀態。
        """
        return self.current_entry

def _create_entry(match, filename, line_num):
    """將正則的具名群組轉成事件 dict，保留檔名、首行行號與續行累積欄位。

    message 保存首行訊息，message_body 保存一般續行，stacktrace_lines 保存堆疊；
    full_text 則供關鍵字比對，包含首行與所有後續行。

    Args:
        match: 包含 timestamp、thread、level、logger、message 的正則匹配。
        filename: 事件所屬來源檔名，用於出現位置與原文定位。
        line_num: 原始日誌從 1 起算的實體行號。

    Returns:
        LogEntry: 含首行欄位與空續行容器的新 dict。

    Raises:
        IndexError: 匹配物件缺少必要的具名群組。
    """
    timestamp_str = match.group("timestamp")
    thread = match.group("thread")
    level = match.group("level")
    logger = match.group("logger")
    message = match.group("message")

    entry: LogEntry = {
        "timestamp": timestamp_str,
        "thread": thread,
        "level": level,
        "logger": logger,
        "message": message,
        "filename": filename,
        "line_num": line_num,
        "stacktrace_lines": [],
        "message_body": "",
        "full_text": message,
    }
    mdc = extract_mdc(match)
    if mdc:
        entry["mdc"] = mdc
    return entry

def _is_entry_in_time_range(entry, start_time, end_time):
    """以含起訖邊界的方式判斷時間；無法解析的時間不納入結果。

    Args:
        entry: 目前解析事件的 dict；續行函式會原地累加內容。
        start_time: 解析時間起點，含邊界；None 表示不限制。
        end_time: 解析時間終點，含邊界；None 表示不限制。

    Returns:
        bool: 時間可解析且位於含邊界的範圍內時為 True。
    """
    entry_time = _parse_entry_time(entry["timestamp"])
    if entry_time is None:
        return False
    if start_time and entry_time < start_time:
        return False
    return not end_time or entry_time <= end_time

def _parse_entry_time(timestamp_str):
    """以時間戳前 19 字元建立本地時區 datetime；此處比較精度為秒。

    原始時間字串仍保留在事件內；無法按此格式解析時回傳 None，交給上層排除。

    Args:
        timestamp_str: 原始時間字串；此流程以前 19 字元解析至秒。

    Returns:
        datetime | None: 本地時區且精度到秒的時間；格式無效時為 None。
    """
    try:
        return to_local_timezone(datetime.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None

def _append_continuation_line(entry, line_num, line):
    """原地更新 full_text，再將續行分配至一般本文或帶原始行號的堆疊。

    Args:
        entry: 目前解析事件的 dict；續行函式會原地累加內容。
        line_num: 原始日誌從 1 起算的實體行號。
        line: 目前讀到的原文行，可能保留換行。

    Returns:
        None: 原地更新事件的完整文字、本文或堆疊。
    """
    line_text = line.rstrip("\n")
    entry["full_text"] += f"\n{line_text}"

    if _is_stacktrace_line(entry, line_text):
        entry["stacktrace_lines"].append(f"{line_num:5}: {line}")
        return

    entry["message_body"] = _append_message_body(entry["message_body"], line_text)

def _append_message_body(message_body, line_text):
    """回傳接上續行的新字串；第一行不多加換行，也不修改呼叫者的原字串。

    Args:
        message_body: 已累積的一般續行本文。
        line_text: 已去除尾端換行的續行文字。

    Returns:
        str: 接上續行的新本文字串。
    """
    if not message_body:
        return line_text
    return f"{message_body}\n{line_text}"

def _is_stacktrace_line(entry, line_text):
    """判斷是否為 Java 堆疊；開始累積堆疊後，後續行都沿用堆疊分類。

    Args:
        entry: 目前解析事件的 dict；續行函式會原地累加內容。
        line_text: 已去除尾端換行的續行文字。

    Returns:
        bool: 本行應歸入堆疊時為 True。
    """
    if entry["stacktrace_lines"]:
        return True

    stripped = line_text.strip()
    if not stripped:
        return False

    if stripped.startswith(("at ", "Caused by:", "Suppressed:", "... ")):
        return True

    return bool(_STACKTRACE_HEADER_RE.match(stripped))
