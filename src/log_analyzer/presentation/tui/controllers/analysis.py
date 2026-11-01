"""TUI 的分析生命週期：固定表單、必要時確認 Pattern、執行、顯示與收尾。

從 action_run_analysis / _schedule_analysis_start 進入 _run_analysis_lifecycle。
async def 定義協程，await 等待非同步工作完成；create_task 則排入事件迴圈，
讓按鈕事件能先返回。真正的資料解析交給應用服務，不放在畫面事件裡。
"""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from rich.text import Text
from textual.widgets import Button, Checkbox, Input, RichLog, Static
from log_analyzer.application.analysis.service import (
    AnalysisRequest,
    NoMatchingLogsError,
)
from log_analyzer.domain.analysis_control import (
    AnalysisCancelled,
    AnalysisControl,
    AnalysisProgress,
)
from log_analyzer.domain.logback.xml import (
    LogbackPatternAssessment,
    LogbackPatternCandidate,
)
from log_analyzer.presentation.tui.screens.pattern_confirmation import (
    LogbackPatternConfirmationScreen,
)
from log_analyzer.presentation.tui.views import (
    build_error_view,
    build_loading_view,
    build_zero_result_view,
)
from log_analyzer.presentation.encoding_warnings import format_encoding_warning

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PendingPatternAnalysis:
    """暫存等待使用者確認 Pattern 的請求，防止確認後套用到已修改的表單。"""
    assessment: LogbackPatternAssessment
    request: AnalysisRequest
    display_mode: str
    result_box: RichLog
    original_focused: object | None


class AnalysisLifecycleMixin:
    """為 LogAnalyzerApp 提供分析操作；依賴 App 的元件、狀態及其他 mixin。

    Mixin 是由主類別繼承的一組方法，不是可單獨啟動的 App；
    因此 self.query_one、self._last_result 等成員是在主 App 中提供。
    """
    def _handle_unexpected_error(self, result_box: RichLog, exc: Exception) -> None:
        """記錄完整錯誤堆疊；有舊結果則還原，否則顯示失敗畫面。

        Args:
            result_box: 用來顯示摘要或錯誤的 RichLog 元件。
            exc: 要記錄或呈現的例外物件。

        Returns:
            None: 記錄失敗並恢復舊結果或顯示錯誤。
        """
        logger.exception("TUI 分析發生未預期錯誤", exc_info=exc)
        if not self.is_running:
            return
        if self._last_result is not None:
            self._restore_previous_result(
                result_box,
                "本次分析失敗，已保留上次成功結果",
                str(exc),
            )
            return
        self._set_result(result_box, build_error_view("執行失敗", str(exc)))
        self._set_result_status("failed")

    def _handle_analysis_error(self, result_box: RichLog, exc: Exception) -> None:
        """將已知例外轉成畫面訊息；本次失敗或零結果不覆蓋上次成功結果。

        Args:
            result_box: 用來顯示摘要或錯誤的 RichLog 元件。
            exc: 要記錄或呈現的例外物件。

        Returns:
            None: 呈現已知錯誤並保留上次成功結果。
        """
        if not self.is_running:
            return

        if self._last_result is not None:
            if isinstance(exc, NoMatchingLogsError):
                notice = "本次分析沒有符合條件的日誌，已保留上次成功結果"
                detail = None
                encoding_warning = format_encoding_warning(exc.diagnostics)
            elif isinstance(exc, PermissionError):
                notice = "本次分析權限不足，已保留上次成功結果"
                detail = str(exc)
                encoding_warning = None
            elif isinstance(exc, FileNotFoundError):
                notice = "本次分析找不到來源，已保留上次成功結果"
                detail = str(exc)
                encoding_warning = None
            else:
                notice = "本次分析失敗，已保留上次成功結果"
                detail = str(exc)
                encoding_warning = None
            self._restore_previous_result(
                result_box,
                notice,
                detail,
                encoding_warning=encoding_warning,
            )
            return

        if isinstance(exc, NoMatchingLogsError):
            view = build_zero_result_view(exc.diagnostics)
            status = "no_results"
        elif isinstance(exc, PermissionError):
            view = build_error_view("權限不足", str(exc))
            status = "failed"
        elif isinstance(exc, FileNotFoundError):
            view = build_error_view("找不到資料夾", str(exc))
            status = "failed"
        else:
            title = (
                "無可分析資料"
                if str(exc).startswith("找不到符合條件的 log")
                else "輸入錯誤"
            )
            view = build_error_view(title, str(exc))
            status = "failed"

        self._set_result(result_box, view)
        self._set_result_status(status)

    def _handle_analysis_cancelled(self, result_box: RichLog) -> None:
        """取消後恢復上次結果；若沒有舊結果，顯示已取消而非一般失敗。

        Args:
            result_box: 用來顯示摘要或錯誤的 RichLog 元件。

        Returns:
            None: 恢復舊結果或顯示已取消。
        """
        if not self.is_running:
            return
        if self._restore_previous_result(
            result_box,
            "分析已取消，已保留上次成功結果",
        ):
            return
        self._set_result(result_box, build_error_view("分析已取消", "目前分析已停止。"))
        self._set_result_status("cancelled")

    async def action_run_analysis(self) -> None:
        """非同步操作入口：匯出區焦點走匯出，其餘情況開始分析並防止重入。
        Returns:
            None: 分流操作並等待本輪生命週期結束。
        """
        if getattr(self, "_operation_in_progress", False):
            return
        if self._focused_inside_export_settings():
            if getattr(self.focused, "id", None) != "browse_output_path":
                await self.action_confirm_export()
            return

        self._operation_in_progress = True
        await self._run_analysis_lifecycle()

    def action_schedule_analysis(self) -> None:
        """供同步動作入口排程分析；實際防重入與建立 task 交給下一層。
        Returns:
            None: 轉交排程入口。
        """
        self._schedule_analysis_start()

    def _schedule_analysis_start(self) -> None:
        """先鎖定操作狀態，再建立背景協程，避免連續按鍵排入多次分析。

        保存 task 可供測試或其他流程追蹤；create_task 只排程，不等於工作完成。
        Returns:
            None: 工作已排程，或因忙碌而忽略；不表示分析已完成。
        """
        if getattr(self, "_operation_in_progress", False):
            return
        if self._focused_inside_export_settings():
            if getattr(self.focused, "id", None) != "browse_output_path":
                asyncio.create_task(self.action_confirm_export())
            return

        self._operation_in_progress = True
        self._scheduled_analysis_task = asyncio.create_task(
            self._run_analysis_lifecycle()
        )

    async def _run_analysis_lifecycle(self) -> None:
        """固定來源與表單請求，選擇直接執行或開啟 Pattern 確認視窗。

        replace 是 dataclasses 的複製工具：產生新的 request，保留其他欄位。
        若等待確認，執行責任交給回呼；其餘路徑由 finally 統一恢復操作狀態。
        Returns:
            None: 分析完成，或責任已交給 Pattern 確認回呼。
        """
        self._restored_encoding_warning = None
        run_button = self.query_one("#run", Button)
        result_box = self.query_one("#result-box", RichLog)
        original_focused = self.focused
        confirmation_pending = False

        try:
            log_dir = self.query_one("#path", Input).value.strip() or "."
            try:
                source = self._resolve_log_source(log_dir)
            except (OSError, ValueError) as exc:
                self._clear_source_scope_preview(str(exc))
                raise
            self._show_source_scope(source)
            self._autofill_logback_settings(
                source.path, selected_files=source.filenames
            )

            manual_override = self.query_one("#manual_pattern_override", Checkbox).value
            if manual_override:
                self._load_selected_logback_pattern_for_analysis(
                    source.path,
                    selected_files=source.filenames,
                )
            else:
                self._analysis_log_pattern = None

            request, display_mode = self._collect_form_values()
            request = replace(
                request, path=source.path, selected_files=source.filenames
            )
            self._pending_result_identity = self._capture_result_form_identity()

            assessment = (
                None
                if manual_override
                else self._assess_logback_pattern_for_analysis(source)
            )
            if assessment is not None and assessment.status == "uncertain":
                self._pending_pattern_analysis = _PendingPatternAnalysis(
                    assessment=assessment,
                    request=request,
                    display_mode=display_mode,
                    result_box=result_box,
                    original_focused=original_focused,
                )
                self.push_screen(
                    LogbackPatternConfirmationScreen(assessment),
                    callback=self._on_pattern_confirmation,
                )
                confirmation_pending = True
                return

            if not manual_override:
                request = replace(request, log_pattern=self._analysis_log_pattern)
            await self._execute_frozen_analysis(
                request, display_mode, result_box, original_focused
            )
        except (
            NoMatchingLogsError,
            PermissionError,
            FileNotFoundError,
            ValueError,
        ) as exc:
            self._handle_analysis_error(result_box, exc)
        except Exception as exc:  # noqa: BLE001
            self._handle_unexpected_error(result_box, exc)
        finally:
            if not confirmation_pending:
                self._finish_analysis_operation(original_focused, run_button)

    def _on_pattern_confirmation(
        self, candidate: LogbackPatternCandidate | None
    ) -> None:
        """接收確認視窗結果，驗證候選來源後，用先前固定的請求續跑。

        candidate=None 表示取消；候選必須是這輪 assessment 中的同一個物件，
        再以 create_task 啟動續跑，避免同步視窗回呼直接等待長時間分析。

        Args:
            candidate: 本輪 XML 評估中的候選；None 表示取消。

        Returns:
            None: 驗證候選後排程續跑；取消或過期候選不執行。
        """
        pending = self._pending_pattern_analysis
        self._pending_pattern_analysis = None
        if pending is None:
            return

        self._operation_in_progress = False
        if candidate is None or not self.is_running:
            return
        if (
            not any(item is candidate for item in pending.assessment.candidates)
            or candidate.error is not None
        ):
            return

        self._analysis_log_pattern = candidate.pattern
        self._set_pattern_preview(
            f"已確認 XML（{Path(candidate.xml_path).name}）",
            candidate.pattern,
            seed_pattern=candidate.pattern,
        )
        self.query_one("#logback-xml-status", Static).update(
            f"已確認使用 {candidate.name}，命中 {candidate.matches}/{candidate.checked}"
        )
        request = replace(pending.request, log_pattern=candidate.pattern)
        self._operation_in_progress = True
        asyncio.create_task(
            self._resume_confirmed_pattern_analysis(
                request,
                pending.display_mode,
                pending.result_box,
                pending.original_focused,
            )
        )

    async def _resume_confirmed_pattern_analysis(
        self,
        request: AnalysisRequest,
        display_mode: str,
        result_box: RichLog,
        original_focused: object | None,
    ) -> None:
        """執行已確認的請求，無論成功或失敗都恢復按鈕與焦點。

        Args:
            request: 本輪固定的 AnalysisRequest，包含來源與篩選條件。
            display_mode: 既有流程保留的顯示模式值。
            result_box: 用來顯示摘要或錯誤的 RichLog 元件。
            original_focused: 分析前的焦點元件；None 表示沒有可還原焦點。

        Returns:
            None: 續跑完成後恢復操作狀態。
        """
        run_button: Button | None = None
        try:
            if not self.is_running:
                return
            run_button = self.query_one("#run", Button)
            await self._execute_frozen_analysis(
                request, display_mode, result_box, original_focused
            )
        finally:
            self._finish_analysis_operation(original_focused, run_button)

    async def _execute_frozen_analysis(
        self,
        request: AnalysisRequest,
        display_mode: str,
        result_box: RichLog,
        original_focused: object | None,
    ) -> None:
        """切換執行畫面、建立控制器並等待分析；成功才顯示新的結果。

        請求已固定，不再讀表單條件。舊結果保留供失敗／取消時恢復；
        進度由工作執行緒送回事件迴圈，operation_id 用來識別是否仍為這輪工作。

        Args:
            request: 本輪固定的 AnalysisRequest，包含來源與篩選條件。
            display_mode: 既有流程保留的顯示模式值。
            result_box: 用來顯示摘要或錯誤的 RichLog 元件。
            original_focused: 分析前的焦點元件；None 表示沒有可還原焦點。

        Returns:
            None: 顯示成功、取消或錯誤狀態，不回傳分析結果。
        """
        run_button = self.query_one("#run", Button)
        self._set_export_settings_visible(False)
        self.query_one("#export-status", Static).update("")
        previous_result = self._last_result
        if previous_result is not None:
            self._dismiss_event_browser_for_result(previous_result)

        # 確認 Pattern 後才進入執行狀態並停用按鈕。
        self.set_focus(None)
        run_button.disabled = True
        self._set_result_status("running")
        self._set_analysis_cancel_button(True)
        if previous_result is None:
            self._set_result(result_box, build_loading_view())
        else:
            self._set_result(
                result_box,
                self._build_result_dashboard(previous_result),
            )

        loop = asyncio.get_running_loop()
        operation_id = object()

        def queue_progress(progress: AnalysisProgress) -> None:
            """把工作執行緒的進度排回 UI 迴圈，避免跨執行緒直接更新元件。

            Args:
                progress: 不可變的 AnalysisProgress 快照。

            Returns:
                None: 將進度排回 UI 迴圈；迴圈已關閉時忽略。
            """
            try:
                loop.call_soon_threadsafe(
                    self._apply_analysis_progress, operation_id, progress
                )
            except RuntimeError:
                # App 關閉時，工作執行緒最後的回報可能晚於事件迴圈關閉。
                return

        control = AnalysisControl(progress_callback=queue_progress)
        self._active_analysis_operation_id = operation_id
        self._analysis_control = control
        self._set_analysis_progress(AnalysisProgress("starting", None, 0, 0, 0))

        try:
            self._save_recent_analysis_state(request, display_mode)
            result = await self._analyze_request(request)
            control.raise_if_cancelled()
            self._display_analysis_result(result_box, result)
        except AnalysisCancelled:
            if "result" in locals() and result is not self._last_result:
                self._release_result(result, close_browser=False)
            self._handle_analysis_cancelled(result_box)
        except (
            NoMatchingLogsError,
            PermissionError,
            FileNotFoundError,
            ValueError,
        ) as exc:
            self._handle_analysis_error(result_box, exc)
        except Exception as exc:  # noqa: BLE001
            self._handle_unexpected_error(result_box, exc)

    def _finish_analysis_operation(
        self,
        original_focused: object | None,
        run_button: Button | None,
    ) -> None:
        """清除本輪控制狀態、恢復可用焦點，並處理延後到工作完成的結果清理。

        Args:
            original_focused: 分析前的焦點元件；None 表示沒有可還原焦點。
            run_button: 本輪開始分析按鈕；元件不可用時可為 None。

        Returns:
            None: 恢復按鈕／焦點並執行延後清理。
        """
        self._operation_in_progress = False
        if getattr(self, "_active_analysis_operation_id", None) is not None:
            self._active_analysis_operation_id = None
            self._analysis_control = None
        if self.is_running:
            self._set_analysis_cancel_button(False)
            if run_button is not None:
                run_button.disabled = False
            if (
                original_focused
                and getattr(original_focused, "is_mounted", False)
                and not getattr(original_focused, "disabled", False)
            ):
                self.set_focus(original_focused)
        if getattr(self, "_release_result_when_operation_finishes", False):
            self._release_result_when_operation_finishes = False
            self._release_last_result(close_browser=False)
        if self.is_running:
            self.call_after_refresh(self._sync_result_context_notice)

    async def action_retry_without_time(self) -> None:
        """清空時間條件後重新分析，用於零結果畫面的快速修正操作。
        Returns:
            None: 移除時間條件後等待重新分析。
        """
        for field_id in ("start_date", "start_time", "end_date", "end_time"):
            self.query_one(f"#{field_id}", Input).value = ""
        await self.action_run_analysis()

    async def action_retry_without_level(self) -> None:
        """改為不限 Level 並同步選項狀態，再重新分析。
        Returns:
            None: 取消層級限制後等待重新分析。
        """
        self.query_one("#level_unrestricted", Checkbox).value = True
        self._sync_level_controls()
        await self.action_run_analysis()

    async def action_retry_without_keyword(self) -> None:
        """移除關鍵字條件後重新分析，其餘條件沿用目前表單。
        Returns:
            None: 清空關鍵字後等待重新分析。
        """
        self.query_one("#keyword", Input).value = ""
        await self.action_run_analysis()

    def _set_analysis_cancel_button(self, visible: bool) -> None:
        """依分析狀態顯示／停用取消按鈕，並還原下一輪可用的按鈕文字。

        Args:
            visible: 是否顯示並啟用取消按鈕。

        Returns:
            None: 更新取消按鈕的可見性與可用狀態。
        """
        if not self.is_running:
            return
        button = self.query_one("#cancel-analysis", Button)
        button.display = visible
        button.disabled = not visible
        button.label = "取消分析"

    def action_cancel_analysis(self) -> None:
        """送出合作式取消要求並顯示取消中；必須等解析器檢查訊號後才真正結束。
        Returns:
            None: 送出取消要求，實際停止由工作檢查點決定。
        """
        control = getattr(self, "_analysis_control", None)
        if control is None or not getattr(self, "_operation_in_progress", False):
            return
        if control.is_cancelled:
            return
        control.cancel()
        if self.is_running:
            button = self.query_one("#cancel-analysis", Button)
            button.disabled = True
            button.label = "取消中…"
            self.query_one("#result-status", Static).update(
                "狀態：取消中，等待目前工作停止"
            )

    def _apply_analysis_progress(
        self,
        operation_id: object,
        progress: AnalysisProgress,
    ) -> None:
        """只呈現仍有效的這輪進度，忽略 App 關閉、取消或上一輪晚到的通知。

        Args:
            operation_id: 本輪工作識別物件，用於排除晚到的舊進度。
            progress: 不可變的 AnalysisProgress 快照。

        Returns:
            None: 有效的本輪進度才送入畫面。
        """
        control = getattr(self, "_analysis_control", None)
        if (
            not self.is_running
            or not getattr(self, "_operation_in_progress", False)
            or getattr(self, "_active_analysis_operation_id", None) is not operation_id
            or (control is not None and control.is_cancelled)
        ):
            return
        self._set_analysis_progress(progress)

    def _set_analysis_progress(self, progress: AnalysisProgress) -> None:
        """把進度快照轉成階段、檔名及行數文字；Rich Text 避免檔名被當成標記。

        Args:
            progress: 不可變的 AnalysisProgress 快照。

        Returns:
            None: 更新進度文字。
        """
        phase_names = {
            "starting": "準備",
            "scanning": "掃描",
            "grouping": "整理",
            "sorting": "排序",
            "persisting": "儲存",
            "summarizing": "摘要",
        }
        phase_name = phase_names.get(progress.phase, progress.phase)
        parts = [f"狀態：分析中｜{phase_name}"]
        if progress.filename:
            parts.append(progress.filename)
        if progress.files_total:
            parts.append(f"檔案 {progress.files_completed}/{progress.files_total}")
        parts.append(f"{progress.lines_processed:,} 行")
        self.query_one("#result-status", Static).update(Text("｜".join(parts)))
