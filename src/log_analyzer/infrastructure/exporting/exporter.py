"""將已分析結果串流寫成報表，處理依 Level／大小分檔及暫存發布。

閱讀主線：export_with_options → _stage_export_with_options →
_export_streaming / _export_by_level → _LevelFileWriter。
CSV、JSON、Markdown 的文字格式交給各自模組；檔案發布保護交給 ExportTransaction。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ...domain.log_types import LogEntry
from .csv import render_csv_prefix, render_csv_summary, serialize_csv_log
from .fields import normalize_export_fields
from .json import (
    render_json_prefix,
    render_json_summary,
    render_json_suffix,
    serialize_json_log,
)
from .markdown import render_markdown_prefix, render_markdown_summary, serialize_markdown_log
from .transaction import ExportTransaction

MAX_EXPORT_BYTES_PER_FILE = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """匯出輸入：統計、可迭代明細、目標與格式；凍結欄位不會凍結內部容器。"""
    counts: Mapping[str, int]
    matched_logs: Iterable[LogEntry]
    output_path: str
    format: str = "csv"
    max_export_bytes: int | None = None
    split_by_level: bool = False
    fields: tuple[str, ...] | None = None
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class _LevelExportContext:
    """內部共用設定；output 是暫存路徑，final_output 用於摘要列出正式路徑。"""
    counts: Mapping[str, int]
    matched_logs: Iterable[LogEntry]
    output: Path
    format_name: str
    threshold: int
    active_levels: list[str]
    fields: tuple[str, ...] | None = None
    final_output: Path | None = None


def export_results(  # noqa: PLR0913 - public legacy API retained for backward compatibility
    counts: Mapping[str, int],
    matched_logs: Iterable[LogEntry],
    output_path: str,
    format: str = "csv",
    max_export_bytes: int | None = None,
    split_by_level: bool = False,
    *,
    fields: tuple[str, ...] | None = None,
    overwrite: bool = False,
):
    """保留舊版多參數介面，轉成 ExportOptions 後執行匯出。

    counts 提供層級統計，matched_logs 只需可迭代，不要求一次載入 list。
    回傳包含摘要及分割報表的實際路徑清單；format 可為 csv、json 或 md。

    Args:
        counts: 每個 Level 的事件次數，不是群組數。
        matched_logs: 可迭代的分析明細，可能為 list 或磁碟結果儲存器。
        output_path: 預計發布的主要報表路徑。
        format: 輸出格式：csv、json 或 md。
        max_export_bytes: 每個分檔的位元組門檻；None 使用匯出器預設。
        split_by_level: 是否在有多個有效層級時分開輸出。
        fields: 明細欄位選擇；None 使用預設欄位。
        overwrite: 是否明確允許覆蓋這次輸出的同名一般檔案。

    Returns:
        list[str]: 已發布的摘要與報表路徑。

    Raises:
        ExportConflictError: 目標已存在且未授權覆蓋。
        OSError: 目的路徑不安全、寫入或發布／還原失敗。
        ValueError: 指定欄位等匯出設定無效。
    """
    return export_with_options(
        ExportOptions(
            counts=counts,
            matched_logs=matched_logs,
            output_path=output_path,
            format=format,
            max_export_bytes=max_export_bytes,
            split_by_level=split_by_level,
            fields=fields,
            overwrite=overwrite,
        )
    )


def export_with_options(
    options: ExportOptions,
    *,
    protected_paths=(),
) -> list[str]:
    """先在暫存區產生完整報表，再發布至目標並回傳正式路徑。

    protected_paths 指定不能覆蓋的來源；finally 確保準備或發布失敗時也嘗試清理。
    此函式不負責掃描日誌，僅消費 options.matched_logs 中已有的分析結果。

    Args:
        options: ExportOptions，包含已分析明細及輸出設定。
        protected_paths: 禁止被本次輸出覆蓋的來源路徑集合。

    Returns:
        list[str]: 通過驗證並成功發布的報表路徑。

    Raises:
        ExportConflictError: 目標已存在且未授權覆蓋。
        OSError: 目的路徑不安全、寫入或發布／還原失敗。
        ValueError: 指定欄位等匯出設定無效。
    """
    transaction = ExportTransaction(
        options.output_path,
        overwrite=options.overwrite,
        protected_paths=protected_paths,
    )
    try:
        staged_paths = _stage_export_with_options(options, transaction)
        return transaction.publish(staged_paths)
    finally:
        transaction.cleanup()


def _stage_export_with_options(options: ExportOptions, transaction: ExportTransaction) -> list[str]:
    """正規化欄位與門檻，選擇分 Level 或直接串流寫入，回傳暫存路徑。

    不在這裡發布檔案，讓上層可以先把證據清單也準備好再一起 publish。
    只有多個有效 Level 才依層級分檔，避免單一 Level 多產生一組檔案。

    Args:
        options: ExportOptions，包含已分析明細及輸出設定。
        transaction: 負責暫存、衝突檢查與發布的 ExportTransaction。

    Returns:
        list[str]: 已完成但尚未發布的報表暫存路徑。

    Raises:
        ValueError: 欄位設定無效。
        OSError: 暫存寫入失敗。
    """
    fields = (
        None
        if options.fields is None
        else normalize_export_fields(options.fields)
    )
    output = Path(options.output_path)
    staged_output = transaction.stage_path(output.name)
    format_name = options.format.lower()
    threshold = (
        MAX_EXPORT_BYTES_PER_FILE
        if options.max_export_bytes is None
        else options.max_export_bytes
    )
    active_levels = [str(level) for level, count in options.counts.items() if count > 0]
    context = _LevelExportContext(
        counts=options.counts,
        matched_logs=options.matched_logs,
        output=staged_output,
        format_name=format_name,
        threshold=threshold,
        active_levels=active_levels,
        fields=fields,
        final_output=output,
    )
    if options.split_by_level and len(active_levels) > 1:
        return _export_by_level(context)
    return _export_streaming(context)


def _export_by_level(context: _LevelExportContext) -> list[str]:
    """每個有效 Level 各建 writer，一次遍歷明細後產生總摘要與各層級報表。

    失敗時盡力關檔及清理已寫暫存，再重新拋出原例外，交由交易層處理後續清理。

    Args:
        context: 固定格式、門檻、統計與暫存目標的 _LevelExportContext。

    Returns:
        list[str]: 摘要在前、各 Level 報表在後的暫存路徑。

    Raises:
        OSError: 分檔寫入、結尾或摘要建立失敗。
    """
    base_name = context.output.stem
    suffix = context.output.suffix or f".{context.format_name}"
    writers: dict[str, _LevelFileWriter] = {}
    created_paths: list[str] = []

    try:
        for level in context.active_levels:
            writers[level] = _LevelFileWriter(
                context.output.with_name(
                    f"{base_name}_{_safe_filename_part(level)}{suffix}"
                ),
                {level: context.counts[level]},
                context.format_name,
                context.threshold,
                context.fields,
            )

        for log in context.matched_logs:
            level = str(log.get("level", ""))
            writer = writers.get(level)
            if writer is None:
                continue
            writer.write(log)

        for level in context.active_levels:
            created_paths.extend(writers[level].close())

        summary_path = context.output.with_name(f"{base_name}_summary{suffix}")
        _write_text(
            summary_path,
            _render_summary(
                context.counts,
                _summary_paths(context, created_paths),
                context.format_name,
            ),
            _encoding_for(context.format_name),
        )
        return [str(summary_path), *created_paths]
    except BaseException:
        for writer in writers.values():
            try:
                writer.abort()
            except BaseException:  # noqa: BLE001 - preserve the original render error after best-effort cleanup
                pass
        for file_path in created_paths:
            try:
                _unlink_if_exists(Path(file_path))
            except BaseException:  # noqa: BLE001 - keep cleaning other staged paths
                pass
        raise


class _LevelFileWriter:
    """單一報表串流的寫入狀態，負責大小門檻、檔案輪替與格式結尾。

    一個事件不拆成兩個檔案，因此單筆內容本身過大時仍會超過門檻。
    門檻以編碼後的位元組計算，不是 Python 字串的字元數。
    """
    def __init__(
        self,
        output: Path,
        counts,
        format_name: str,
        threshold: int,
        fields: tuple[str, ...] | None = None,
    ) -> None:
        """開啟第一個暫存檔、寫入前綴，並預留 JSON 等格式結尾需要的大小。

        Args:
            output: 目前 writer 的暫存輸出路徑。
            counts: 每個 Level 的事件次數，不是群組數。
            format_name: 已正規化的小寫報表格式。
            threshold: 分檔位元組門檻；單筆事件不拆開。
            fields: 明細欄位選擇；None 使用預設欄位。

        Returns:
            None: 開啟第一個分檔並初始化大小與輪替狀態。

        Raises:
            OSError: 檔案或前綴無法寫入。
        """
        self.output = output
        self.counts = counts
        self.format_name = format_name
        self.threshold = threshold
        self.fields = fields
        self.base_name = output.stem
        self.suffix = output.suffix or f".{format_name}"
        self.encoding = _encoding_for(format_name)
        report_suffix = _report_suffix(format_name)
        self.suffix_size = _rendered_size(report_suffix, format_name) if report_suffix else 0
        self.part_paths: list[Path] = []
        self.current_part_index = 1
        self.current_has_logs = False
        self.current_part_log_index = 0
        self.split_occurred = False
        self.current_path = output
        self.closed = False
        self.current_handle = None
        try:
            self.current_handle = _open_part_file(
                output, counts, format_name, self.encoding, fields
            )
            self.current_size = _rendered_size(
                _report_prefix(counts, format_name, fields), format_name
            )
        except BaseException:
            if self.current_handle is not None:
                try:
                    self.current_handle.close()
                except BaseException:  # noqa: BLE001 - preserve the constructor's original failure
                    pass
            self.closed = True
            try:
                _unlink_if_exists(output)
            except BaseException:  # noqa: BLE001 - keep the original constructor failure
                pass
            raise

    def write(self, log) -> None:
        """序列化一個事件；非空檔案將超過門檻時，先輪替再重新計算該筆編號。

        Args:
            log: 已分組的單筆事件，提供欄位讀取介面。

        Returns:
            None: 寫入一筆完整事件，必要時先輪替。

        Raises:
            OSError: 事件寫入或分檔輪替失敗。
        """
        while True:
            self.current_part_log_index += 1
            block, block_size = _render_log_block(
                self.current_part_log_index,
                log,
                self.format_name,
                self.current_has_logs,
                self.fields,
            )

            if (
                self.current_has_logs
                and self.current_size + block_size + self.suffix_size > self.threshold
            ):
                self._rotate_part()
                continue

            self.current_handle.write(block)
            self.current_size += block_size
            self.current_has_logs = True
            break

    def close(self) -> list[str]:
        """補上格式結尾並關檔，回傳本 writer 的路徑；重複關閉回傳空清單。
        Returns:
            list[str]: 本 writer 完成的路徑；已關閉時回空清單。

        Raises:
            OSError: 格式結尾或關檔失敗。
        """
        if self.closed:
            return []

        self.current_handle = _close_part_file(self.current_handle, self.format_name)
        self.closed = True
        if self.split_occurred:
            self.part_paths.append(self.current_path)
            return [str(path) for path in self.part_paths]
        return [str(self.output)]

    def abort(self) -> None:
        """中止尚未發布的輸出，盡力關檔並刪除本 writer 建立的各個暫存檔。
        Returns:
            None: 中止寫入並嘗試清理自有暫存。

        Raises:
            OSError: 關檔失敗；仍先嘗試清理暫存後傳回。
        """
        close_error: BaseException | None = None
        if not self.closed and self.current_handle is not None:
            try:
                self.current_handle.close()
            except BaseException as error:  # noqa: BLE001 - always finish best-effort staged-file cleanup
                close_error = error
            finally:
                self.closed = True
        _unlink_if_exists(self.output)
        _unlink_if_exists(self.current_path)
        for path in self.part_paths:
            _unlink_if_exists(path)
        if close_error is not None:
            raise close_error

    def write_empty_markdown_message(self) -> None:
        """Markdown 沒有明細時寫入明確提示，並更新已寫入大小。
        Returns:
            None: 寫入空結果提示並更新大小。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        """
        message = "無符合條件的記錄\n"
        self.current_handle.write(message)
        self.current_size += _rendered_size(message, self.format_name)

    def _rotate_part(self) -> None:
        """結束目前分檔並開啟下一檔，首次分割時將原檔改名為 part001。
        Returns:
            None: 關閉目前檔案並開啟下一分檔。

        Raises:
            OSError: 來源或暫存檔案無法讀寫時向上傳遞。
        """
        _close_part_file(self.current_handle, self.format_name)

        next_part_path = self.output.with_name(
            f"{self.base_name}_part{self.current_part_index:03d}{self.suffix}"
        )
        if not self.split_occurred:
            os.replace(self.current_path, next_part_path)
        self.part_paths.append(next_part_path if not self.split_occurred else self.current_path)

        self.current_path = self.output.with_name(
            f"{self.base_name}_part{self.current_part_index + 1:03d}{self.suffix}"
        )
        self.current_handle = _open_part_file(
            self.current_path,
            self.counts,
            self.format_name,
            self.encoding,
            self.fields,
        )
        self.current_part_index += 1
        self.split_occurred = True
        self.current_size = _rendered_size(
            _report_prefix(self.counts, self.format_name, self.fields), self.format_name
        )
        self.current_has_logs = False
        self.current_part_log_index = 0


def _export_streaming(context: _LevelExportContext) -> list[str]:
    """逐筆寫入同一串流；發生大小分割才額外建立列出分檔的摘要。

    Args:
        context: 固定格式、門檻、統計與暫存目標的 _LevelExportContext。

    Returns:
        list[str]: 單檔路徑，或摘要與所有分檔路徑。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
    """
    writer = _LevelFileWriter(
        context.output,
        context.counts,
        context.format_name,
        context.threshold,
        context.fields,
    )
    has_any_logs = False

    created_paths: list[str] = []
    try:
        for log in context.matched_logs:
            has_any_logs = True
            writer.write(log)

        if context.format_name == 'md' and not has_any_logs:
            writer.write_empty_markdown_message()

        created_paths = writer.close()
        if not writer.split_occurred:
            return created_paths

        summary_path = context.output.with_name(
            f"{context.output.stem}_summary{context.output.suffix or f'.{context.format_name}'}"
        )
        _write_text(
            summary_path,
            _render_summary(
                context.counts,
                _summary_paths(context, created_paths),
                context.format_name,
            ),
            _encoding_for(context.format_name),
        )
        return [str(summary_path), *created_paths]
    except BaseException:
        try:
            writer.abort()
        except BaseException:  # noqa: BLE001 - preserve the original render error after best-effort cleanup
            pass
        for file_path in created_paths:
            try:
                _unlink_if_exists(Path(file_path))
            except BaseException:  # noqa: BLE001 - keep cleaning other staged paths
                pass
        raise


def _render_log_block(
    index: int,
    log,
    format_name: str,
    has_previous_logs: bool,
    fields: tuple[str, ...] | None = None,
) -> tuple[str, int]:
    """回傳（單筆格式文字，編碼大小）；JSON 另依是否已有資料加入逗號。

    Args:
        index: 目前分檔內從 1 起算的事件編號。
        log: 已分組的單筆事件，提供欄位讀取介面。
        format_name: 已正規化的小寫報表格式。
        has_previous_logs: 目前檔案是否已寫入事件，決定 JSON 是否加逗號。
        fields: 明細欄位選擇；None 使用預設欄位。

    Returns:
        tuple[str, int]: 單筆文字及編碼後位元組大小。
    """
    if format_name == 'json':
        prefix = ",\n" if has_previous_logs else ""
        block = prefix + serialize_json_log(log, fields=fields)
        return block, _rendered_size(block, format_name)
    if format_name == 'md':
        block = serialize_markdown_log(index, log, fields=fields)
        return block, _rendered_size(block, format_name)

    # CSV 以逐行寫入，不需要額外分隔符
    block = serialize_csv_log(log, fields)
    return block, _rendered_size(block, format_name)


def _report_prefix(
    counts, format_name: str, fields: tuple[str, ...] | None = None
) -> str:
    """依格式產生檔案前綴，例如欄位標題或 JSON 的外層結構。

    Args:
        counts: 每個 Level 的事件次數，不是群組數。
        format_name: 已正規化的小寫報表格式。
        fields: 明細欄位選擇；None 使用預設欄位。

    Returns:
        str: 所選格式的檔頭或前綴。
    """
    if format_name == 'json':
        return render_json_prefix(counts, fields)
    if format_name == 'md':
        return render_markdown_prefix(counts, fields)
    return render_csv_prefix(counts, fields)


def _report_suffix(format_name: str) -> str:
    """回傳完整報表需要的結尾；JSON 需關閉結構，其餘格式回空字串。

    Args:
        format_name: 已正規化的小寫報表格式。

    Returns:
        str: JSON 結尾或其他格式的空字串。
    """
    if format_name == 'json':
        return render_json_suffix()
    return ""


def _open_part_file(
    path: Path,
    counts,
    format_name: str,
    encoding: str,
    fields: tuple[str, ...] | None = None,
):
    """開啟分檔並寫前綴；newline='' 防止 Windows 換行轉換破壞大小計算。

    成功時把檔案控制權交給 writer；前綴寫入失敗則先關檔再傳回例外。

    Args:
        path: 要開啟或刪除的暫存檔路徑。
        counts: 每個 Level 的事件次數，不是群組數。
        format_name: 已正規化的小寫報表格式。
        encoding: 實際寫檔的字元編碼。
        fields: 明細欄位選擇；None 使用預設欄位。

    Returns:
        TextIO: 已寫入前綴的檔案，由 writer 負責關閉。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
    """
    file_handle = path.open("w", encoding=encoding, newline="")
    try:
        file_handle.write(_report_prefix(counts, format_name, fields))
    except BaseException:
        try:
            file_handle.close()
        except BaseException:  # noqa: BLE001 - preserve the original write failure
            pass
        raise
    return file_handle


def _close_part_file(file_handle, format_name: str):
    """寫入格式結尾並關檔；即使寫結尾失敗，finally 仍會嘗試關閉檔案。

    Args:
        file_handle: 已開啟、由此流程接手關閉的文字檔案物件。
        format_name: 已正規化的小寫報表格式。

    Returns:
        None: 寫完結尾後關閉檔案。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
    """
    try:
        file_handle.write(_report_suffix(format_name))
    finally:
        file_handle.close()


def _render_summary(counts, split_files, format_name: str) -> str:
    """委派給各格式模組建立統計與分檔清單文字，不直接寫入檔案。

    Args:
        counts: 每個 Level 的事件次數，不是群組數。
        split_files: 要列在摘要中的正式分檔路徑。
        format_name: 已正規化的小寫報表格式。

    Returns:
        str: 統計與分檔清單的格式文字。
    """
    if format_name == 'json':
        return render_json_summary(counts, split_files)
    if format_name == 'md':
        return render_markdown_summary(counts, split_files)
    return render_csv_summary(counts, split_files)


def _summary_paths(context: _LevelExportContext, staged_paths: list[str]) -> list[str]:
    """將暫存檔名映射成正式目錄路徑，避免摘要洩漏已清理的暫存位置。

    Args:
        context: 固定格式、門檻、統計與暫存目標的 _LevelExportContext。
        staged_paths: 尚未發布的報表暫存路徑清單。

    Returns:
        list[str]: 對應正式目的資料夾的報表路徑。
    """
    final_output = context.final_output or context.output
    return [str(final_output.parent / Path(path).name) for path in staged_paths]


def _write_text(output: Path, text: str, encoding: str) -> None:
    """用指定編碼完整寫入一份文字檔，保留原字串換行並自動關檔。

    Args:
        output: 要完整寫入文字內容的檔案路徑。
        text: 待檢查或寫出的文字。
        encoding: 實際寫檔的字元編碼。

    Returns:
        None: 完整寫入文字並關檔。

    Raises:
        OSError: 來源或暫存檔案無法讀寫時向上傳遞。
    """
    with open(output, 'w', encoding=encoding, newline='') as file_handle:
        file_handle.write(text)


def _safe_filename_part(value: str) -> str:
    """將 Level 文字轉成可用的檔名片段；不允許字元以底線替代。

    Args:
        value: 要清理為檔名片段的 Level 文字。

    Returns:
        str: 安全檔名片段，空值改為 UNKNOWN。
    """
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return safe or "UNKNOWN"


def _unlink_if_exists(path: Path) -> None:
    """盡力刪除暫存檔；缺檔或檔案系統錯誤不蓋過原本的匯出例外。

    Args:
        path: 要開啟或刪除的暫存檔路徑。

    Returns:
        None: 盡力刪除檔案，忽略 OSError。
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _encoding_for(format_name: str) -> str:
    """CSV 使用含 BOM 的 UTF-8，JSON／Markdown 使用 UTF-8。

    Args:
        format_name: 已正規化的小寫報表格式。

    Returns:
        str: CSV 為 utf-8-sig，其餘為 utf-8。
    """
    return 'utf-8-sig' if format_name == 'csv' else 'utf-8'


def _rendered_size(text: str, format_name: str) -> int:
    """計算格式編碼後的位元組長度，供分檔門檻估算使用。

    Args:
        text: 待檢查或寫出的文字。
        format_name: 已正規化的小寫報表格式。

    Returns:
        int: 字串依輸出編碼計算的位元組數。
    """
    return len(text.encode(_encoding_for(format_name)))
