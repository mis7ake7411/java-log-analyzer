"""事件的文字篩選、重複分組與排序規則；由 parser 的掃描流程呼叫。

解析中的單筆事件是 dict，合併後則以 GroupedLog 保存。讀本檔可觀察
Python 如何用 tuple 作為 dict 的鍵，以及 dataclass 如何相容既有字典讀法。
"""

from __future__ import annotations

from dataclasses import dataclass

from .log_types import OccurrenceCoordinate

LEVEL_SORT_ORDER = {
    "ERROR": 0,
    "WARN": 1,
    "WARNING": 1,
    "INFO": 2,
    "DEBUG": 3,
    "TRACE": 4,
}

_LOG_FIELDS = {
    "count",
    "line_numbers",
    "timestamp",
    "last_timestamp",
    "level",
    "thread",
    "logger",
    "filename",
    "occurrences",
    "message",
    "message_body",
    "stacktrace",
}


@dataclass(slots=True)
class GroupedLog:
    """相同內容的一組事件，保存次數與各次出現的檔名、行號、時間和執行緒。

    __getitem__ / get / __contains__ 讓物件可保留部分 dict 用法，並非完整 Mapping。
    line_numbers 首次用 tuple，重複時才轉成 list，避免每個單次事件都先建可變清單。
    """
    count: int
    line_numbers: list[int] | tuple[int, ...]
    timestamp: str
    last_timestamp: str
    level: str
    thread: str
    logger: str
    filename: str
    occurrences: list[OccurrenceCoordinate]
    message: str
    message_body: str
    stacktrace: str

    def __getitem__(self, key):
        """支援 group['level'] 等讀法；未知欄位拋出 KeyError，行號則回傳新 list。

        Args:
            key: 要讀取或檢查的欄位名稱。

        Returns:
            object: 欄位值；行號欄位會轉成新的 list。

        Raises:
            KeyError: 欄位名稱不受支援。
        """
        if key == "line_numbers":
            return normalize_line_numbers(self.line_numbers)
        if key in _LOG_FIELDS:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        """提供與 dict.get 相同的缺值讀法，未知欄位回傳 default。

        Args:
            key: 要讀取或檢查的欄位名稱。
            default: 欄位不存在時回傳的值。

        Returns:
            object: 欄位值或 default。
        """
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        """支援 'level' in group，判斷欄位名稱是否在允許集合內。

        Args:
            key: 要讀取或檢查的欄位名稱。

        Returns:
            bool: key 是否為允許欄位。
        """
        return key in _LOG_FIELDS

    def to_dict(self):
        """建立普通 dict 與出現位置清單，方便交給需要字典資料的呼叫者。
        Returns:
            dict: 含獨立行號清單與出現位置 dict 的一般字典。
        """
        return {
            "count": self.count,
            "line_numbers": normalize_line_numbers(self.line_numbers),
            "timestamp": self.timestamp,
            "last_timestamp": self.last_timestamp,
            "level": self.level,
            "thread": self.thread,
            "logger": self.logger,
            "filename": self.filename,
            "occurrences": [
                {
                    "filename": occurrence["filename"],
                    "line_number": occurrence["line_number"],
                    "timestamp": occurrence["timestamp"],
                    "thread": occurrence["thread"],
                }
                for occurrence in self.occurrences
            ],
            "message": self.message,
            "message_body": self.message_body,
            "stacktrace": self.stacktrace,
        }


def normalize_keyword(keyword, ignore_case):
    """忽略大小寫時預先將關鍵字轉小寫；來源文字在 should_include 才轉換。

    Args:
        keyword: 關鍵字；None 或空字串表示不限制。
        ignore_case: 是否忽略關鍵字比對的大小寫。

    Returns:
        str | None: 適合後續比對的關鍵字。
    """
    if keyword and ignore_case:
        return keyword.lower()
    return keyword


def sort_key(entry, sort_by):
    """回傳供 sorted 比較的 tuple；Level 模式先比嚴重程度，再比時間字串。

    Python 依 tuple 元素順序比較；未知 Level 排在已知層級之後。
    時間模式的 (timestamp,) 是單元素 tuple，尾端逗號不可省略。

    Args:
        entry: 供讀取層級及時間欄位的事件群組。
        sort_by: 排序方式：time 或 level。

    Returns:
        tuple: 依 Level 與時間，或只按時間比較的鍵。
    """
    timestamp = entry["timestamp"]
    if sort_by == "level":
        return (
            LEVEL_SORT_ORDER.get(entry["level"], 99),
            timestamp,
        )
    return (timestamp,)


def commit_entry(grouped_logs, counts, entry, keyword, ignore_case):
    """檢查包含續行的 full_text，命中才更新 counts 與 grouped_logs。

    回傳 bool 表示這筆事件是否納入；兩個容器會原地修改，不建立新容器。

    Args:
        grouped_logs: 以事件識別 tuple 為鍵的群組 dict；此函式會原地更新。
        counts: 每個 Level 的事件次數，不是群組數。
        entry: 已解析的事件 dict。
        keyword: 已由 normalize_keyword 依 ignore_case 正規化的關鍵字。
        ignore_case: 是否忽略關鍵字比對的大小寫。

    Returns:
        bool: 通過文字篩選並納入統計時為 True。
    """
    if not should_include(entry["full_text"], keyword, ignore_case):
        return False

    counts[entry["level"]] += 1
    add_to_grouped_logs(grouped_logs, entry)
    return True


def add_to_grouped_logs(grouped_logs, entry):
    """按層級、Logger、訊息、續行本文與堆疊，把事件加入或合併至群組。

    dict 的鍵使用不可變 tuple；時間、檔名與執行緒不參與識別，
    改存進 occurrences，因此相同內容可跨檔案合併，仍保留每次的來源座標。

    Args:
        grouped_logs: 以事件識別 tuple 為鍵的群組 dict；此函式會原地更新。
        entry: 已解析的事件 dict。

    Returns:
        None: 原地建立或累加事件群組。
    """
    stacktrace_text = "".join(entry["stacktrace_lines"]).strip()
    key = (
        entry["level"],
        entry["logger"],
        entry["message"],
        entry.get("message_body", ""),
        stacktrace_text,
    )
    occurrence: OccurrenceCoordinate = {
        "filename": entry["filename"],
        "line_number": entry["line_num"],
        "timestamp": entry["timestamp"],
        "thread": entry["thread"],
    }

    if key in grouped_logs:
        grouped_logs[key].count += 1
        grouped_logs[key].last_timestamp = entry["timestamp"]
        grouped_logs[key].occurrences.append(occurrence)
        # 記錄重複項出現的行號
        _append_line_number(grouped_logs[key], entry["line_num"])
    else:
        grouped_logs[key] = GroupedLog(
            count=1,
            line_numbers=(entry["line_num"],),
            timestamp=entry["timestamp"],
            last_timestamp=entry["timestamp"],
            level=entry["level"],
            thread=entry["thread"],
            logger=entry["logger"],
            filename=entry["filename"],
            occurrences=[occurrence],
            message=entry["message"],
            message_body=entry.get("message_body", ""),
            stacktrace=stacktrace_text,
        )


def _append_line_number(entry, line_num):
    """重複事件需要增加行號時，才將原本的 tuple 轉成 list 並原地追加。

    Args:
        entry: 要原地追加行號的 GroupedLog。
        line_num: 原始日誌從 1 起算的實體行號。

    Returns:
        None: 原地擴充群組的行號清單。
    """
    if not isinstance(entry.line_numbers, list):
        entry.line_numbers = normalize_line_numbers(entry.line_numbers)
    entry.line_numbers.append(line_num)


def normalize_line_numbers(line_numbers):
    """把 list、tuple、單一行號或 None 轉成新 list，避免讀者改到原集合。

    Args:
        line_numbers: list、tuple、單一行號或 None。

    Returns:
        list[int]: 正規化的新清單，不與傳入 list 共用容器。
    """
    if isinstance(line_numbers, list):
        return list(line_numbers)
    if isinstance(line_numbers, tuple):
        return list(line_numbers)
    if line_numbers is None:
        return []
    return [line_numbers]


def should_include(text, keyword, ignore_case):
    """以子字串包含關係判斷命中；無關鍵字表示全部通過，不使用正則。

    忽略大小寫時，呼叫者須先用 normalize_keyword 處理 keyword。

    Args:
        text: 待檢查或寫出的文字。
        keyword: 已由 normalize_keyword 依 ignore_case 正規化的關鍵字。
        ignore_case: 是否忽略關鍵字比對的大小寫。

    Returns:
        bool: 未限制關鍵字或文字包含關鍵字時為 True。
    """
    if not keyword:
        return True

    target_text = text.lower() if ignore_case else text
    return keyword in target_text
