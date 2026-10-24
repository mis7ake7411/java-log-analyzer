from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from rich.text import Text
from textual.widgets import Button, Checkbox, Input, RichLog, Static
from log_analyzer.application.analysis_service import (
    AnalysisRequest,
    NoMatchingLogsError,
)
from log_analyzer.domain.analysis_control import (
    AnalysisCancelled,
    AnalysisControl,
    AnalysisProgress,
)
from log_analyzer.domain.logback_xml import (
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
    assessment: LogbackPatternAssessment
    request: AnalysisRequest
    display_mode: str
    result_box: RichLog
    original_focused: object | None


class AnalysisLifecycleMixin:
    def _handle_unexpected_error(self, result_box: RichLog, exc: Exception) -> None:
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
        if getattr(self, "_operation_in_progress", False):
            return
        if self._focused_inside_export_settings():
            if getattr(self.focused, "id", None) != "browse_output_path":
                await self.action_confirm_export()
            return

        self._operation_in_progress = True
        await self._run_analysis_lifecycle()

    def action_schedule_analysis(self) -> None:
        self._schedule_analysis_start()

    def _schedule_analysis_start(self) -> None:
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
            try:
                loop.call_soon_threadsafe(
                    self._apply_analysis_progress, operation_id, progress
                )
            except RuntimeError:
                # The event loop may already be closed while a worker finishes its last callback.
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
        for field_id in ("start_date", "start_time", "end_date", "end_time"):
            self.query_one(f"#{field_id}", Input).value = ""
        await self.action_run_analysis()

    async def action_retry_without_level(self) -> None:
        self.query_one("#level_unrestricted", Checkbox).value = True
        self._sync_level_controls()
        await self.action_run_analysis()

    async def action_retry_without_keyword(self) -> None:
        self.query_one("#keyword", Input).value = ""
        await self.action_run_analysis()

    def _set_analysis_cancel_button(self, visible: bool) -> None:
        if not self.is_running:
            return
        button = self.query_one("#cancel-analysis", Button)
        button.display = visible
        button.disabled = not visible
        button.label = "取消分析"

    def action_cancel_analysis(self) -> None:
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
