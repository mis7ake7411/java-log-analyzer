"""日誌解析主線：逐行組成事件 → 篩選與分組 → 排序 → 保存可重讀的結果。

從 parse_log_directory 開始閱讀，再看 iter_logs 與 parsing 子套件。
iter_ 開頭的函式多使用 yield：呼叫時先取得生成器，迭代時才實際執行。
此模組只處理資料，不認識 Textual 畫面或報表格式。
"""

from __future__ import annotations

import heapq
import os
import pickle
import tempfile
from array import array
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime

from .analysis_control import AnalysisControl
from .logback.pattern import DEFAULT_LOGBACK_REGEX, compile_logback_pattern
from .log_sources import select_log_source
from .matched_logs_store import MatchedLogsStore
from .parsing import aggregation, entries as entry_assembly, scanner
from log_analyzer.domain.log_types import MatchedLogs, ScanDiagnostics, SourceSnapshot
from .timezone import to_local_timezone

MAX_SORT_GROUPS_IN_MEMORY = 5000
MAX_GROUPS_IN_MEMORY = 5000
MAX_GROUP_SHARDS = 32


@dataclass(frozen=True)
class ParseOptions:
    """解析條件的資料容器；frozen=True 避免掃描途中被改寫。

    levels=None 表示不限層級，空 tuple 則不會選入任何層級；
    selected_files=None 表示讓來源選擇器決定檔案範圍。
    """
    start_time: datetime | None = None
    end_time: datetime | None = None
    keyword: str | None = None
    ignore_case: bool = False
    log_pattern: str | None = None
    sort_by: str = "time"
    levels: tuple[str, ...] | None = None
    selected_files: tuple[str, ...] | None = None


def parse_logs(  # noqa: PLR0913 - public legacy API retained for backward compatibility
    directory,
    start_time=None,
    end_time=None,
    keyword=None,
    ignore_case=False,
    log_pattern=None,
    sort_by="time",
    levels=None,
):
    """保留舊版位置參數介面，轉成 ParseOptions 後交給 parse_log_directory。

    回傳（符合條件的各 Level 次數，可重複迭代的分組結果儲存器）。
    第二項不保證為 list；儲存器使用完畢後應呼叫 release 清除暫存。

    Args:
        directory: 來源檔案或資料夾；檔案範圍由來源選擇器決定。
        start_time: 解析時間起點，含邊界；None 表示不限制。
        end_time: 解析時間終點，含邊界；None 表示不限制。
        keyword: 關鍵字；None 或空字串表示不限制。
        ignore_case: 是否忽略關鍵字比對的大小寫。
        log_pattern: 支援的 Logback Pattern；None 使用內建格式。
        sort_by: 排序方式：time 或 level。
        levels: 選定層級；None 表示不限，空集合表示不選任何層級。

    Returns:
        tuple[Counter[str], MatchedLogs]: 每層級命中次數與分組結果儲存器。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 來源範圍無效或 Pattern 不受支援時停止處理。
    """
    return parse_log_directory(
        directory,
        ParseOptions(
            start_time,
            end_time,
            keyword,
            ignore_case,
            log_pattern,
            sort_by,
            levels,
        ),
    )


def parse_log_directory(
    directory: str,
    options: ParseOptions,
    *,
    control: AnalysisControl | None = None,
) -> tuple[Counter[str], MatchedLogs]:
    """解析選定來源並回傳（層級計數，依指定順序保存的事件群組）。

    Counter 是預設計數為零的計數容器；counts 統計每次命中，而非群組數。
    control 是可選的取消／進度介面。最終結果寫入暫存檔，方便 TUI 分頁
    及重複匯出；呼叫者負責在結果不再使用時釋放。

    Args:
        directory: 來源檔案或資料夾；檔案範圍由來源選擇器決定。
        options: ParseOptions，指定時間、層級、關鍵字與來源範圍。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        tuple[Counter[str], MatchedLogs]: 每層級命中次數與已排序結果儲存器。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 來源範圍無效或 Pattern 不受支援時停止處理。
    """
    if control is not None:
        control.raise_if_cancelled()
    counts = Counter()
    diagnostics = ScanDiagnostics()
    grouped_logs, _spilled_to_disk, source_states = _collect_grouped_logs(
        directory,
        options,
        counts,
        diagnostics,
        control=control,
    )
    sorted_logs = iter_sorted_logs(grouped_logs, options.sort_by, control=control)

    return counts, _persist_matched_logs(sorted_logs, source_states, diagnostics, control=control)


def _collect_grouped_logs(directory, options, counts, diagnostics=None, *, control=None):
    """掃描並依序套用時間、Level、關鍵字條件，原地更新 counts 與 diagnostics。

    回傳（群組集合，是否曾寫入磁碟分片，來源快照）。群組到門檻時先落盤，
    最後重新讀取並合併；TemporaryDirectory 離開 with 時自動清除分片。
    最後合併仍會建立記憶體集合，不能視為整段流程都使用固定大小記憶體。

    Args:
        directory: 來源檔案或資料夾；檔案範圍由來源選擇器決定。
        options: ParseOptions，指定時間、層級、關鍵字與來源範圍。
        counts: 每個 Level 的事件次數，不是群組數。
        diagnostics: 掃描診斷物件；傳入後可能被原地更新。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        tuple: 群組集合、是否曾寫分片、來源快照。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 來源範圍無效或 Pattern 不受支援時停止處理。
    """
    search_keyword = aggregation.normalize_keyword(options.keyword, options.ignore_case)
    selected_levels = _normalize_levels(options.levels)
    grouped_logs = {}
    spilled_to_disk = False
    source_states: dict[str, SourceSnapshot] = {}

    with tempfile.TemporaryDirectory() as temp_dir:
        shard_paths = _build_group_shard_paths(temp_dir)
        _report_phase(control, "grouping")

        for entry in iter_logs(
            directory,
            options.start_time,
            options.end_time,
            options.log_pattern,
            source_states,
            diagnostics=diagnostics,
            selected_files=options.selected_files,
            control=control,
        ):
            _raise_if_cancelled(control)
            if selected_levels is not None and entry["level"].upper() not in selected_levels:
                continue
            if diagnostics is not None:
                diagnostics.after_level += 1
            if aggregation.commit_entry(grouped_logs, counts, entry, search_keyword, options.ignore_case):
                if diagnostics is not None:
                    diagnostics.after_keyword += 1
            if len(grouped_logs) >= MAX_GROUPS_IN_MEMORY:
                _spill_grouped_logs(grouped_logs, shard_paths, control=control)
                grouped_logs.clear()
                spilled_to_disk = True

        if spilled_to_disk:
            _spill_grouped_logs(grouped_logs, shard_paths, control=control)
            grouped_logs = _load_grouped_logs_from_shards(shard_paths, control=control)
        else:
            grouped_logs = grouped_logs.values()

        _raise_if_cancelled(control)
    return grouped_logs, spilled_to_disk, source_states


def _normalize_levels(levels):
    """將層級轉成去空白的大寫集合；保留 None 與空集合的不同語意。

    Args:
        levels: 選定層級；None 表示不限，空集合表示不選任何層級。

    Returns:
        set[str] | None: 正規化集合；None 與空集合保留不同語意。
    """
    if levels is None:
        return None
    return {str(level).strip().upper() for level in levels if str(level).strip()}


def iter_logs(  # noqa: PLR0913 - preserve the existing positional API
    directory,
    start_time=None,
    end_time=None,
    log_pattern=None,
    source_states: dict[str, SourceSnapshot] | None = None,
    *,
    diagnostics: ScanDiagnostics | None = None,
    selected_files: tuple[str, ...] | None = None,
    control: AnalysisControl | None = None,
):
    """逐檔產生完整且通過時間篩選的事件；Level 與關鍵字由上層過濾。

    yield / yield from 讓上層逐筆消費，不必先讀完整份原文。
    source_states 與 diagnostics 是呼叫者傳入的可變物件，會在掃描時更新；
    檔案前後的大小及修改時間用於標記來源是否穩定，不會修改來源日誌。

    Args:
        directory: 來源檔案或資料夾；檔案範圍由來源選擇器決定。
        start_time: 解析時間起點，含邊界；None 表示不限制。
        end_time: 解析時間終點，含邊界；None 表示不限制。
        log_pattern: 支援的 Logback Pattern；None 使用內建格式。
        source_states: 按檔名保存的大小、修改時間與穩定性快照。
        diagnostics: 掃描診斷物件；傳入後可能被原地更新。
        selected_files: 固定檔名範圍；None 讓來源選擇器決定，空 tuple 不選任何檔案。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        Iterator[LogEntry]: 通過時間篩選的完整事件；其他條件由上層處理。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
        ValueError: 來源範圍無效或 Pattern 不受支援時停止處理。
    """
    if start_time is not None:
        start_time = to_local_timezone(start_time)
    if end_time is not None:
        end_time = to_local_timezone(end_time)

    entry_pattern = compile_logback_pattern(log_pattern) if log_pattern else DEFAULT_LOGBACK_REGEX
    if source_states is None:
        source_states = {}

    if not os.path.exists(directory):
        raise FileNotFoundError(f"找不到目錄: {directory}")

    source = select_log_source(directory, selected_files=selected_files)
    files_total = len(source.filenames)
    lines_processed = 0
    if control is not None and files_total == 0:
        control.report_progress("scanning", files_completed=0, files_total=0, force=True)

    for file_index, filename in enumerate(source.filenames):
        _raise_if_cancelled(control)
        filepath = os.path.join(source.root, filename)
        before = os.stat(filepath)
        if diagnostics is not None:
            diagnostics.files_scanned += 1
        accumulator = entry_assembly._EntryAccumulator(filename, start_time, end_time, diagnostics=diagnostics)
        if control is not None:
            control.report_progress(
                "scanning",
                filename=filename,
                files_completed=file_index,
                files_total=files_total,
                lines_processed=lines_processed,
                force=True,
            )

        def on_line() -> None:
            """每讀一行更新共用行數，並讓背景掃描有機會觀察取消訊號。
            Returns:
                None: 更新外層共用行數並回報進度。

            Raises:
                AnalysisCancelled: 已收到取消要求。
            """
            # nonlocal 指向外層 iter_logs 的變數，不是在這裡建立新的區域變數。
            nonlocal lines_processed
            lines_processed += 1
            if control is not None:
                control.raise_if_cancelled()
                control.report_progress(
                    "scanning",
                    filename=filename,
                    files_completed=file_index,
                    files_total=files_total,
                    lines_processed=lines_processed,
                )

        file_has_encoding_error = False

        def on_encoding_error() -> None:
            """累計解碼異常行數，同一檔案的異常檔數只加一次。
            Returns:
                None: 有診斷物件時累加異常行／檔數。
            """
            nonlocal file_has_encoding_error
            if diagnostics is None:
                return
            diagnostics.encoding_error_lines += 1
            if not file_has_encoding_error:
                file_has_encoding_error = True
                diagnostics.encoding_error_files += 1

        scanned_lines = scanner.iter_scanned_lines(
            filepath,
            entry_pattern,
            diagnostics=diagnostics,
            on_line=on_line if control is not None else None,
            on_encoding_error=on_encoding_error if diagnostics is not None else None,
        )
        with closing(scanned_lines):
            yield from entry_assembly.iter_entries(scanned_lines, accumulator)
        _raise_if_cancelled(control)
        after = os.stat(filepath)
        source_states[filename] = {
            "size": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "stable": (
                before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns
            ),
        }
        if control is not None:
            control.report_progress(
                "scanning",
                filename=filename,
                files_completed=file_index + 1,
                files_total=files_total,
                lines_processed=lines_processed,
                force=True,
            )


def iter_sorted_logs(
    entries,
    sort_by="time",
    max_in_memory_groups=MAX_SORT_GROUPS_IN_MEMORY,
    *,
    control: AnalysisControl | None = None,
):
    """以生成器輸出排序後的群組；小集合直接排序，大集合分塊再合併。

    sort_key 決定比較規則。這裡仍先把 entries 收集為 list，再決定排序方式，
    因此使用 yield 不代表所有中間資料都不占記憶體。

    Args:
        entries: 待排序的事件群組集合。
        sort_by: 排序方式：time 或 level。
        max_in_memory_groups: 小集合直接排序的群組數上限。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        Iterator[LogEntry]: 依指定規則排序的事件群組。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
    """
    if control is not None:
        _report_phase(control, "sorting")
    collected_entries = []
    for entry in entries:
        _raise_if_cancelled(control)
        collected_entries.append(entry)
    entries = collected_entries
    _raise_if_cancelled(control)
    if len(entries) <= max_in_memory_groups:
        # sorted 執行途中無法插入取消檢查，所以在呼叫前後各檢查一次。
        sorted_entries = sorted(entries, key=lambda entry: aggregation.sort_key(entry, sort_by))
        _raise_if_cancelled(control)
        yield from sorted_entries
        return

    yield from _iter_sorted_logs_external(entries, sort_by, max_in_memory_groups, control=control)


def _build_group_shard_paths(temp_dir):
    """以列表推導式建立分片檔名；只組合路徑，尚未建立檔案。

    Args:
        temp_dir: 本輪分組分片使用的暫存資料夾。

    Returns:
        list[str]: 各分片的預定路徑，尚未建立檔案。
    """
    return [os.path.join(temp_dir, f"group_shard_{index:03d}.pkl") for index in range(MAX_GROUP_SHARDS)]


def _spill_grouped_logs(grouped_logs, shard_paths, *, control=None):
    """把目前群組追加寫入對應分片；由呼叫者決定何時清空記憶體集合。

    pickle 將 Python 物件轉成位元組；這些檔案只供程式讀回自己建立的暫存，
    不應拿來載入外部提供的 pickle 檔案。

    Args:
        grouped_logs: 目前待寫入分片的群組 dict；寫入本身不會清空它。
        shard_paths: 各群組分片的暫存檔路徑清單。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        None: 將目前群組追加到分片檔。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
    """
    if not grouped_logs:
        return

    shard_entries = {}
    for log in grouped_logs.values():
        _raise_if_cancelled(control)
        shard_index = _group_shard_index(log, len(shard_paths))
        shard_entries.setdefault(shard_index, []).append(log)

    for shard_index, entries in shard_entries.items():
        _raise_if_cancelled(control)
        with open(shard_paths[shard_index], "ab") as file_handle:
            for entry in entries:
                _raise_if_cancelled(control)
                pickle.dump(entry, file_handle, protocol=pickle.HIGHEST_PROTOCOL)


def _load_grouped_logs_from_shards(shard_paths, *, control=None):
    """讀回所有存在的分片，合併跨批次的相同事件，再回傳群組 values 檢視。

    Args:
        shard_paths: 各群組分片的暫存檔路徑清單。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        Iterable[LogEntry]: 重新合併群組後的 dict values 檢視。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
    """
    grouped_logs = {}

    for path in shard_paths:
        _raise_if_cancelled(control)
        if not os.path.exists(path):
            continue

        with open(path, "rb") as file_handle:
            while True:
                try:
                    entry = pickle.load(file_handle)
                except EOFError:
                    break
                _merge_grouped_log(grouped_logs, entry)
                _raise_if_cancelled(control)

    return grouped_logs.values()


def _merge_grouped_log(grouped_logs, entry):
    """原地合併同鍵群組的次數與出現位置；首次出現的鍵直接保存 entry。

    Args:
        grouped_logs: 以事件識別 tuple 為鍵的群組 dict；此函式會原地更新。
        entry: 已合併的 GroupedLog，作為分組識別或合併來源。

    Returns:
        None: 原地更新 grouped_logs。
    """
    key = _group_log_key(entry)

    if key in grouped_logs:
        grouped_logs[key].count += entry.count
        grouped_logs[key].last_timestamp = entry.last_timestamp
        if not isinstance(grouped_logs[key].line_numbers, list):
            grouped_logs[key].line_numbers = aggregation.normalize_line_numbers(grouped_logs[key].line_numbers)
        grouped_logs[key].line_numbers.extend(aggregation.normalize_line_numbers(entry.line_numbers))
        grouped_logs[key].occurrences.extend(entry.occurrences)
        return

    grouped_logs[key] = entry


def _group_shard_index(entry, shard_count):
    """用群組鍵的雜湊餘數選分片；同次執行中同鍵一定進入同一分片。

    Args:
        entry: 已合併的 GroupedLog，作為分組識別或合併來源。
        shard_count: 分片總數，必須大於零。

    Returns:
        int: 大於等於零且小於 shard_count 的分片編號。

    Raises:
        ZeroDivisionError: shard_count 為零，違反呼叫契約。
    """
    return hash(_group_log_key(entry)) % shard_count


def _group_log_key(entry):
    """以層級、Logger、訊息本文與堆疊組成不可變 tuple，作為分組識別。

    時間、檔名及執行緒屬於出現位置，不在此鍵內，讓跨檔案的相同事件可合併。

    Args:
        entry: 已合併的 GroupedLog，作為分組識別或合併來源。

    Returns:
        tuple: 不包含來源座標的事件內容識別鍵。
    """
    return (
        entry.level,
        entry.logger,
        entry.message,
        entry.message_body,
        entry.stacktrace,
    )


def _persist_matched_logs(logs, source_states, diagnostics=None, *, control=None):
    """逐筆序列化排序結果，回傳持有暫存檔與位元組索引的 MatchedLogsStore。

    delete=False 讓關閉檔案後仍能重新開啟分頁；成功後由儲存器負責刪除。
    發生例外時本函式先清理；獨立的 sentinel 物件用來辨識空迭代器。

    Args:
        logs: 可迭代的事件群組。
        source_states: 按檔名保存的大小、修改時間與穩定性快照。
        diagnostics: 掃描診斷物件；傳入後可能被原地更新。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        MatchedLogsStore: 接手暫存檔所有權的可重讀儲存器。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
    """
    path = None
    try:
        iterator = iter(logs)
        sentinel = object()
        first_log = next(iterator, sentinel)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as file_handle:
            path = file_handle.name
            length = 0
            offsets = array("Q")
            _report_phase(control, "persisting", force=True)
            _raise_if_cancelled(control)
            if first_log is not sentinel:
                _raise_if_cancelled(control)
                offsets.append(file_handle.tell())
                pickle.dump(first_log, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
                length += 1
            for log in iterator:
                _raise_if_cancelled(control)
                offsets.append(file_handle.tell())
                pickle.dump(log, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
                length += 1

        return MatchedLogsStore(
            path=path,
            length=length,
            source_states=source_states,
            diagnostics=diagnostics if diagnostics is not None else ScanDiagnostics(),
            _offsets=offsets,
        )
    except BaseException:
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise


def _iter_sorted_logs_external(entries, sort_by, chunk_size, *, control=None):
    """將已收集的 entries 分塊排序並落盤，再以 heapq.merge 合併有序串流。

    *iterators 將迭代器清單展開成多個參數；yield 逐筆交還合併結果。
    每塊仍以 list 寫入與讀回，此處不是完全固定記憶體用量的外部排序。

    Args:
        entries: 待排序的事件群組集合。
        sort_by: 排序方式：time 或 level。
        chunk_size: 每個排序區塊的群組數，必須大於零。
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        Iterator[LogEntry]: 合併各已排序區塊後的事件群組。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        AnalysisCancelled: control 觀察到取消要求時停止處理。
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        chunk_paths = []
        for index in range(0, len(entries), chunk_size):
            _raise_if_cancelled(control)
            chunk = sorted(entries[index:index + chunk_size], key=lambda entry: aggregation.sort_key(entry, sort_by))
            _raise_if_cancelled(control)
            chunk_path = os.path.join(temp_dir, f"sorted_chunk_{len(chunk_paths):05d}.pkl")
            with open(chunk_path, "wb") as file_handle:
                pickle.dump(chunk, file_handle, protocol=pickle.HIGHEST_PROTOCOL)
            chunk_paths.append(chunk_path)

        iterators = [_load_sorted_chunk(path) for path in chunk_paths]
        for entry in heapq.merge(*iterators, key=lambda entry: aggregation.sort_key(entry, sort_by)):
            _raise_if_cancelled(control)
            yield entry


def _load_sorted_chunk(path):
    """讀回一個已排序 list，再逐筆 yield 給合併器；整塊仍會先載入記憶體。

    Args:
        path: 已排序 pickle 區塊檔案路徑。

    Returns:
        Iterator[LogEntry]: 單一區塊的逐筆結果；區塊本身先整批讀入。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
    """
    with open(path, "rb") as file_handle:
        for entry in pickle.load(file_handle):
            yield entry


def _raise_if_cancelled(control) -> None:
    """有控制器才檢查取消，讓沒有進度介面的 CLI 呼叫也能共用解析流程。

    Args:
        control: 可選 AnalysisControl；用於取消檢查及進度回報。

    Returns:
        None: 未提供 control 或尚未取消時繼續。

    Raises:
        AnalysisCancelled: 控制器已收到取消訊號。
    """
    if control is not None:
        control.raise_if_cancelled()


def _report_phase(control, phase: str, *, force: bool = True) -> None:
    """有控制器時回報階段；force 用來讓階段切換不受行數通知門檻限制。

    Args:
        control: 可選 AnalysisControl；用於取消檢查及進度回報。
        phase: 目前分析階段名稱，例如 scanning 或 sorting。
        force: 是否略過節流門檻立即通知。

    Returns:
        None: 有 control 時轉交進度通知。

    Raises:
        AnalysisCancelled: 回報前已收到取消訊號。
    """
    if control is not None:
        control.report_progress(phase, force=force)
