"""TUI 即時追蹤流程與批次結果所有權。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from textual.containers import Container
from textual.css.query import NoMatches
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static

from log_analyzer.application.analysis.service import AnalysisRequest, AnalysisResult
from log_analyzer.application.live.rebuild import prepare_replacement
from log_analyzer.application.live.runner import LiveRunner
from log_analyzer.application.live.session import LiveSession, TrackingSnapshot
from log_analyzer.application.live.summary import (
    TrackingAnalysisResult,
    build_tracking_summary,
)
from log_analyzer.domain.analysis_control import AnalysisCancelled, AnalysisControl
from log_analyzer.domain.logback.xml import (
    LogbackPatternAssessment,
    LogbackPatternCandidate,
)
from log_analyzer.presentation.tui.components.live_panel import LivePanel
from log_analyzer.presentation.tui.screens.live_event import LiveEventScreen
from log_analyzer.presentation.tui.screens.live_summary_details import (
    LiveSummaryDetailsScreen,
)
from log_analyzer.presentation.tui.screens.pattern_confirmation import (
    LogbackPatternConfirmationScreen,
)
from log_analyzer.presentation.tui.views import build_idle_view


logger = logging.getLogger(__name__)

_LIVE_CONDITION_WIDGET_IDS = (
    "path",
    "browse_path",
    "select-source-files",
    "keyword",
    "clear_keyword",
    "logback_xml_path",
    "browse_logback_xml",
    "manual_pattern_override",
    "log_pattern",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
    "ignore_case",
    "sort_by",
    "level_unrestricted",
    "level_ERROR",
    "level_WARN",
    "level_INFO",
    "level_DEBUG",
    "level_TRACE",
    "select_all_levels",
    "clear_levels",
    "manage-recipes",
)


@dataclass(slots=True)
class _PendingLivePattern:
    """保存待確認的追蹤請求，並把 modal 選擇交回原操作 task。"""

    request: AnalysisRequest
    identity: tuple
    assessment: LogbackPatternAssessment
    result: asyncio.Future[LogbackPatternCandidate | None]


class LiveWorkflowMixin:
    """串接即時追蹤，並讓批次與追蹤摘要結果保有獨立生命週期。"""

    def _initialize_live_workflow(self) -> None:
        self._analysis_mode = "batch"
        self._analysis_mode_sync_values: list[str] = []
        self._live_session: LiveSession | None = None
        self._live_runner: LiveRunner | None = None
        self._live_operation_task: asyncio.Task | None = None
        self._live_operation_name: str | None = None
        self._live_condition_identity: tuple | None = None
        self._live_epoch = 0
        self._live_summary_result: TrackingAnalysisResult | None = None
        self._live_summary_active = False
        self._live_pending_snapshot: TrackingSnapshot | None = None
        self._live_summary_control: AnalysisControl | None = None
        self._pending_live_pattern: _PendingLivePattern | None = None
        self._condition_disabled_state: dict[str, bool] | None = None
        self._active_result_kind = "batch"

        self._batch_result: AnalysisResult | None = self._last_result
        self._batch_result_state = self._result_state
        self._batch_result_form_identity = getattr(
            self, "_result_form_identity", None
        )
        self._batch_pending_result_identity = getattr(
            self, "_pending_result_identity", None
        )
        self._batch_encoding_warning = getattr(
            self, "_restored_encoding_warning", None
        )
        self._batch_result_renderable: object | None = None
        self._batch_export_context = ""
        self._batch_export_status = ""

        panel = self.query_one("#live-panel", LivePanel)
        panel.set_session(None)
        panel.display = False
        self.query_one("#live-summary-metadata", Static).display = False
        self.query_one("#live-summary-details", Button).display = False
        self.query_one("#return-live", Button).display = False
        self.query_one("#analysis-mode", Select).value = "batch"
        self._sync_live_view()
        self._sync_live_controls()

    def _handle_analysis_mode_changed(self, value: object) -> None:
        if self._consume_analysis_mode_sync(value):
            return
        requested_mode = str(value)
        if requested_mode not in {"batch", "live"}:
            self._set_analysis_mode_select(self._analysis_mode)
            return
        if requested_mode == self._analysis_mode:
            return
        if self._mode_switch_blocked():
            self._set_analysis_mode_select(self._analysis_mode)
            self._set_live_notice("追蹤或操作進行中時，請先暫停並等待操作完成，再切換模式。")
            return

        if self._analysis_mode == "batch":
            self._capture_batch_context()

        self._analysis_mode = requested_mode
        self._live_summary_active = False
        if requested_mode == "live":
            self._last_result = None
            self._result_state = "idle"
            self._result_form_identity = None
            self._pending_result_identity = None
            self._active_result_kind = "live-panel"
            self._set_export_settings_visible(False)
            self.query_one("#export-result-context", Static).update("")
            self.query_one("#export-status", Static).update("")
            self.query_one("#live-panel", LivePanel).set_session(self._live_session)
            self._set_live_notice("即時追蹤只支援單一本機、未壓縮日誌檔案。")
        else:
            self._set_export_settings_visible(False)
            self._restore_batch_context()

        self._sync_live_view()
        self._sync_live_controls()

    def _consume_analysis_mode_sync(self, value: object) -> bool:
        if self._analysis_mode_sync_values and self._analysis_mode_sync_values[0] == value:
            self._analysis_mode_sync_values.pop(0)
            return True
        return False

    def _set_analysis_mode_select(self, value: str) -> None:
        select = self.query_one("#analysis-mode", Select)
        if select.value == value:
            return
        self._analysis_mode_sync_values.append(value)
        select.value = value

    def _mode_switch_blocked(self) -> bool:
        live_task = self._live_operation_task
        session = self._live_session
        return bool(
            getattr(self, "_operation_in_progress", False)
            or (live_task is not None and not live_task.done())
            or (
                session is not None
                and session.state in {"tracking", "interrupted"}
            )
        )

    def _sync_live_view(self) -> None:
        if not getattr(self, "is_mounted", False):
            return
        is_live = self._analysis_mode == "live"
        show_summary = is_live and self._live_summary_active
        show_live_panel = is_live and not show_summary
        for selector in (
            "#output-title",
            "#live-summary-metadata",
            "#result-status",
            "#result-context-notice",
            "#result-box",
            "#output-actions",
        ):
            widget = self.query_one(selector)
            widget.display = not show_live_panel

        zero_result_actions = self.query_one("#zero-result-actions", Container)
        if show_live_panel:
            zero_result_actions.display = False
        else:
            # 解除即時面板的暫時隱藏，讓 zero-results-hidden CSS 依結果狀態決定可見性。
            zero_result_actions.styles.display = None

        self.query_one("#live-panel", LivePanel).display = show_live_panel
        self.query_one("#live-summary-metadata", Static).display = show_summary
        self.query_one("#live-summary-details", Button).display = show_summary
        self.query_one("#return-live", Button).display = show_summary
        content = self.query_one("#content")
        if is_live:
            content.add_class("live-mode")
        else:
            content.remove_class("live-mode")
        if show_summary:
            content.add_class("live-summary")
        else:
            content.remove_class("live-summary")
        self.query_one("#output-title", Static).update(
            "追蹤摘要" if show_summary else "即時追蹤" if show_live_panel else "執行結果"
        )

    def _capture_batch_context(self) -> None:
        if self._active_result_kind != "batch":
            return
        self._batch_result = self._last_result
        self._batch_result_state = self._result_state
        self._batch_result_form_identity = getattr(
            self, "_result_form_identity", None
        )
        self._batch_pending_result_identity = getattr(
            self, "_pending_result_identity", None
        )
        self._batch_encoding_warning = getattr(
            self, "_restored_encoding_warning", None
        )
        self._batch_export_context = self.query_one(
            "#export-result-context", Static
        ).render().plain
        self._batch_export_status = self.query_one("#export-status", Static).render().plain

    def _restore_batch_context(self) -> None:
        self._active_result_kind = "batch"
        self._last_result = self._batch_result
        self._result_state = self._batch_result_state
        self._result_form_identity = self._batch_result_form_identity
        self._pending_result_identity = self._batch_pending_result_identity
        self._restored_encoding_warning = self._batch_encoding_warning
        result_box = self.query_one("#result-box", RichLog)
        if self._batch_result is not None:
            renderable = self._build_dashboard_with_restore_warning(
                self._batch_result,
                self._should_compact_dashboard(self.size.width, self.size.height),
            )
        else:
            renderable = self._batch_result_renderable or build_idle_view()
        self._set_result(result_box, renderable)
        self._set_result_status(
            self._batch_result_state,
            self._batch_result
            if self._batch_result_state == "complete"
            else None,
        )
        self.query_one("#export-status", Static).update(self._batch_export_status)
        self.query_one("#export-result-context", Static).update(
            self._batch_export_context
        )
        self._sync_result_context_notice()

    def _set_result(self, result_box: RichLog, renderable: object) -> None:
        super()._set_result(result_box, renderable)
        if getattr(self, "_active_result_kind", "batch") == "batch":
            self._batch_result_renderable = renderable

    def _display_analysis_result(
        self, result_box: RichLog, result: AnalysisResult
    ) -> None:
        super()._display_analysis_result(result_box, result)
        if not isinstance(result, TrackingAnalysisResult):
            self._active_result_kind = "batch"
            self._batch_result = result if self._last_result is result else None
            self._batch_result_state = self._result_state
            self._batch_result_form_identity = getattr(
                self, "_result_form_identity", None
            )
            self._batch_pending_result_identity = getattr(
                self, "_pending_result_identity", None
            )
            self._batch_encoding_warning = getattr(
                self, "_restored_encoding_warning", None
            )
            self._batch_export_context = self.query_one(
                "#export-result-context", Static
            ).render().plain

    def _release_last_result(self, close_browser: bool = True) -> None:
        released = getattr(self, "_last_result", None)
        super()._release_last_result(close_browser=close_browser)
        if released is self._batch_result:
            self._batch_result = None
            self._batch_result_state = "idle"
            self._batch_result_form_identity = None
            self._batch_result_renderable = None
        if released is self._live_summary_result:
            self._live_summary_result = None
            self._live_summary_active = False

    def action_clear_result(self) -> None:
        if self._live_summary_active:
            summary = self._live_summary_result
            self._live_summary_result = None
            self._live_summary_active = False
            if summary is not None:
                self._release_result(summary)
            self._last_result = None
            self._result_state = "idle"
            self._result_form_identity = None
            self._pending_result_identity = None
            self._restored_encoding_warning = None
            self.query_one("#live-summary-metadata", Static).update("")
            self.query_one("#export-status", Static).update("")
            self._set_export_settings_visible(False)
            self._set_result(self.query_one("#result-box", RichLog), build_idle_view())
            self._set_result_status("idle")
            self._active_result_kind = "live-panel"
            self._sync_live_view()
            self._sync_live_controls()
            return

        if self._analysis_mode == "live":
            return

        super().action_clear_result()
        self._batch_result = None
        self._batch_result_state = "idle"
        self._batch_result_form_identity = None
        self._batch_result_renderable = None
        self._batch_export_context = ""
        self._batch_export_status = ""

    def action_browse_events(self) -> None:
        if self._analysis_mode == "live" and not self._live_summary_active:
            return
        super().action_browse_events()

    def action_browse_summary(self) -> None:
        if self._analysis_mode == "live" and not self._live_summary_active:
            return
        super().action_browse_summary()

    def _schedule_analysis_start(self) -> None:
        if self._analysis_mode == "live":
            self._set_live_notice("即時追蹤模式不會啟動批次分析；請使用追蹤開關開始或接續。")
            return
        super()._schedule_analysis_start()
        self._sync_live_controls()

    async def action_run_analysis(self) -> None:
        if self._analysis_mode == "live":
            self._set_live_notice("即時追蹤模式不會啟動批次分析；請使用追蹤開關開始或接續。")
            return
        await super().action_run_analysis()
        self._sync_live_controls()

    def _finish_analysis_operation(self, *args, **kwargs) -> None:
        super()._finish_analysis_operation(*args, **kwargs)
        self._sync_live_controls()

    def _set_export_controls_disabled(self, disabled: bool) -> None:
        super()._set_export_controls_disabled(disabled)
        self._sync_live_controls()

    def _finish_export_operation(self) -> None:
        super()._finish_export_operation()
        self._sync_live_controls()

    def on_live_panel_toggle_tracking(self, message: LivePanel.ToggleTracking) -> None:
        self._schedule_live_operation(
            "追蹤狀態交接", partial(self._toggle_live_tracking, message.enabled)
        )

    def on_live_panel_rebuild_requested(
        self, _message: LivePanel.RebuildRequested
    ) -> None:
        self._schedule_live_operation(
            "重新分析來源", self._rebuild_live_session
        )

    def on_live_panel_summary_requested(
        self, _message: LivePanel.SummaryRequested
    ) -> None:
        if not self._can_schedule_live_operation():
            return
        session = self._live_session
        if session is None:
            self._set_live_notice("尚未開始追蹤，沒有可整理的快照。")
            return
        # 事件送達時立即固定截止序號；摘要建置之後才交給 worker。
        try:
            snapshot = session.snapshot()
        except (OSError, RuntimeError) as exc:
            self._set_live_notice(f"無法建立追蹤快照：{exc}")
            return
        scheduled = self._schedule_live_operation(
            "建立追蹤摘要", partial(self._build_live_summary, snapshot)
        )
        if not scheduled:
            snapshot.release()

    def on_live_panel_stop_requested(self, _message: LivePanel.StopRequested) -> None:
        self._schedule_live_operation("結束追蹤", self._stop_live_session)

    def on_live_panel_open_event(self, message: LivePanel.OpenEvent) -> None:
        if self.is_running:
            self.push_screen(LiveEventScreen(message.event))

    def _can_schedule_live_operation(self) -> bool:
        task = self._live_operation_task
        if task is not None and not task.done():
            return False
        return True

    def _schedule_live_operation(
        self,
        name: str,
        operation: Callable[[], Awaitable[None]] | Awaitable[None],
    ) -> bool:
        if not self._can_schedule_live_operation():
            if inspect.iscoroutine(operation):
                operation.close()
            return False
        awaitable = operation() if callable(operation) else operation
        task = asyncio.create_task(self._run_live_operation(name, awaitable))
        self._live_operation_task = task
        self._live_operation_name = name
        task.add_done_callback(self._live_operation_finished)
        panel = self.query_one("#live-panel", LivePanel)
        panel.set_busy(True)
        self._set_analysis_cancel_button(
            name in {"重新分析來源", "建立追蹤摘要"}
        )
        self._set_live_notice(f"{name}中…")
        self._sync_live_controls()
        return True

    async def _run_live_operation(self, name: str, operation) -> None:
        try:
            await operation
        except asyncio.CancelledError:
            self._set_live_notice(f"{name}已取消；原追蹤資料仍保留。")
        except AnalysisCancelled:
            self._set_live_notice(f"{name}已取消；原追蹤資料仍保留。")
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Live TUI %s failed: %s", name, exc)
            self._set_live_notice(f"{name}失敗：{exc}")
            if self._live_session is not None:
                self.query_one("#live-panel", LivePanel).refresh_session()
        finally:
            if self._live_operation_task is asyncio.current_task():
                self._live_operation_task = None
                self._live_operation_name = None
            if self.is_running:
                self.query_one("#live-panel", LivePanel).set_busy(False)
                self._set_analysis_cancel_button(False)
                self._sync_live_controls()

    def _live_operation_finished(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        logger.error(
            "Unexpected live TUI operation failure",
            exc_info=(type(error), error, error.__traceback__),
        )
        if self.is_running:
            self._set_live_notice(f"即時追蹤操作發生非預期錯誤：{error}")

    async def _toggle_live_tracking(self, enabled: bool) -> None:
        panel = self.query_one("#live-panel", LivePanel)
        session = self._live_session
        runner = self._live_runner

        if not enabled:
            if runner is not None:
                await runner.pause()
            if session is not None:
                panel.refresh_session()
                self._set_live_notice("已暫停；接續時會沿用原條件與讀取位置。")
            else:
                panel.set_session(None)
                self._set_live_notice("即時追蹤尚未開始。")
            return

        if session is not None:
            if self._capture_result_form_identity() != self._live_condition_identity:
                panel.refresh_session()
                self._set_live_notice("條件已變更；請使用「重新分析來源」建立新追蹤。")
                return
            if session.state in {"stopped", "error"}:
                panel.refresh_session()
                raise RuntimeError("此工作階段不能接續，請重新分析來源")
            if runner is None:
                raise RuntimeError("追蹤 runner 不存在，請重新分析來源")
            await runner.start(session.conditions)
            self._set_live_notice("追蹤已接續；沿用原讀取位置與條件。")
            return

        request, identity, assessment = self._collect_live_request()
        if assessment is not None and assessment.status == "uncertain":
            request = await self._confirm_live_pattern(request, identity, assessment)
            if request is None:
                panel.set_session(None)
                self._set_live_notice("Pattern 確認已取消，尚未開始追蹤。")
                return
            identity = self._capture_result_form_identity()

        session = LiveSession(request)
        self._live_condition_identity = identity
        self._publish_live_session(session)
        try:
            await self._live_runner.start(session.conditions)
        except BaseException:
            if self._live_session is not session:
                session.close()
            raise
        self._set_live_notice("追蹤已開始；條件已固定。")

    def _collect_live_request(
        self,
    ) -> tuple[
        AnalysisRequest,
        tuple,
        LogbackPatternAssessment | None,
    ]:
        log_dir = self.query_one("#path", Input).value.strip() or "."
        source = self._resolve_log_source(log_dir)
        self._show_source_scope(source)
        if not source.is_file or len(source.filenames) != 1:
            raise ValueError("即時追蹤只支援單一檔案來源；資料夾來源請改用批次分析。")

        self._autofill_logback_settings(source.path, selected_files=source.filenames)
        manual_override = self.query_one("#manual_pattern_override", Checkbox).value
        if manual_override:
            self._load_selected_logback_pattern_for_analysis(
                source.path,
                selected_files=source.filenames,
            )
        else:
            self._analysis_log_pattern = None

        request, _display_mode = self._collect_form_values()
        request = replace(
            request,
            path=source.path,
            selected_files=source.filenames,
        )
        self._pending_result_identity = self._capture_result_form_identity()
        assessment = (
            None
            if manual_override
            else self._assess_logback_pattern_for_analysis(source)
        )
        if not manual_override:
            request = replace(request, log_pattern=self._analysis_log_pattern)
        identity = self._capture_result_form_identity()
        return request, identity, assessment

    async def _confirm_live_pattern(
        self,
        request: AnalysisRequest,
        identity: tuple,
        assessment: LogbackPatternAssessment,
    ) -> AnalysisRequest | None:
        future: asyncio.Future[LogbackPatternCandidate | None] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_live_pattern = _PendingLivePattern(
            request=request,
            identity=identity,
            assessment=assessment,
            result=future,
        )
        self.push_screen(
            LogbackPatternConfirmationScreen(assessment),
            callback=self._on_live_pattern_confirmation,
        )
        candidate = await future
        pending = self._pending_live_pattern
        if pending is None:
            return None
        self._pending_live_pattern = None
        if candidate is None:
            return None
        if (
            identity != self._capture_result_form_identity()
            or not any(item is candidate for item in assessment.candidates)
            or candidate.error is not None
        ):
            raise ValueError("Pattern 確認已失效，請重新開始追蹤。")

        self._analysis_log_pattern = candidate.pattern
        self._set_pattern_preview(
            f"已確認 XML（{Path(candidate.xml_path).name}）",
            candidate.pattern,
            seed_pattern=candidate.pattern,
        )
        self.query_one("#logback-xml-status", Static).update(
            f"已確認使用 {candidate.name}，命中 {candidate.matches}/{candidate.checked}"
        )
        return replace(request, log_pattern=candidate.pattern)

    def _on_live_pattern_confirmation(
        self, candidate: LogbackPatternCandidate | None
    ) -> None:
        pending = self._pending_live_pattern
        if pending is not None and not pending.result.done():
            pending.result.set_result(candidate)

    def _publish_live_session(self, session: LiveSession) -> None:
        self._live_epoch += 1
        epoch = self._live_epoch
        session_id = session.session_id
        runner = LiveRunner(
            session,
            on_events=partial(self._on_live_events, session_id, epoch),
            on_update=partial(self._on_live_update, session_id, epoch),
        )
        self._live_session = session
        self._live_runner = runner
        self.query_one("#live-panel", LivePanel).set_session(session)
        self._sync_live_controls()

    def _on_live_events(
        self,
        session_id: str,
        epoch: int,
        _events: list[dict],
    ) -> None:
        if not self._is_current_live_session(session_id, epoch):
            return
        if not self.is_running:
            return
        panel = self.query_one("#live-panel", LivePanel)
        if panel.is_mounted:
            panel.refresh_session()

    def _on_live_update(self, session_id: str, epoch: int) -> None:
        if not self._is_current_live_session(session_id, epoch):
            return
        if not self.is_running:
            return
        panel = self.query_one("#live-panel", LivePanel)
        if panel.is_mounted:
            panel.refresh_session()
        self._sync_live_controls()

    def _is_current_live_session(self, session_id: str, epoch: int) -> bool:
        session = self._live_session
        return bool(
            session is not None
            and session.session_id == session_id
            and self._live_epoch == epoch
        )

    async def _rebuild_live_session(self) -> None:
        previous = self._live_session
        previous_runner = self._live_runner
        if previous is None or previous_runner is None:
            raise RuntimeError("尚未開始追蹤，請先開啟追蹤開關。")

        await previous_runner.pause()

        request, identity, assessment = self._collect_live_request()
        if assessment is not None and assessment.status == "uncertain":
            request = await self._confirm_live_pattern(request, identity, assessment)
            if request is None:
                self.query_one("#live-panel", LivePanel).refresh_session()
                self._set_live_notice("已取消重新分析，原追蹤結果仍保留。")
                return
            identity = self._capture_result_form_identity()

        replacement = await prepare_replacement(previous, request)
        self._live_condition_identity = identity
        self._publish_live_session(replacement)
        try:
            previous.close()
        except OSError as exc:
            logger.exception("Unable to close replaced live session")
            self._set_live_notice(f"新追蹤已建立，但舊追蹤暫存清理失敗：{exc}")
            return
        self._set_live_notice("來源已重新分析；新追蹤保持暫停，可檢視後再接續。")

    async def _stop_live_session(self) -> None:
        session = self._live_session
        runner = self._live_runner
        if session is None or runner is None:
            return
        await runner.stop()
        self.query_one("#live-panel", LivePanel).refresh_session()
        self._sync_live_controls()
        self._set_live_notice("追蹤已結束；已保存的事件仍可檢視與整理摘要。")

    async def _build_live_summary(self, snapshot: TrackingSnapshot) -> None:
        self._live_pending_snapshot = snapshot
        control = AnalysisControl()
        self._live_summary_control = control
        self._set_analysis_cancel_button(True)
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(
            None,
            partial(build_tracking_summary, snapshot, control=control),
        )
        try:
            result = await asyncio.shield(worker)
        except AnalysisCancelled:
            snapshot.release()
            self._live_pending_snapshot = None
            self._set_live_notice("追蹤摘要已取消；追蹤工作階段仍保留。")
            return
        except asyncio.CancelledError:
            control.cancel()
            result = None
            try:
                result = await asyncio.shield(worker)
            except AnalysisCancelled:
                pass
            except Exception:
                logger.exception("追蹤摘要取消時 worker 發生錯誤")
            if result is None:
                snapshot.release()
            else:
                self._release_result(result, close_browser=False)
            self._live_pending_snapshot = None
            raise
        except BaseException:
            snapshot.release()
            self._live_pending_snapshot = None
            raise
        finally:
            self._live_summary_control = None
            if self.is_running:
                self._set_analysis_cancel_button(False)

        if not self.is_running:
            self._release_result(result, close_browser=False)
            self._live_pending_snapshot = None
            return
        self._live_pending_snapshot = None
        self._display_live_summary(result)

    def _display_live_summary(self, result: TrackingAnalysisResult) -> None:
        self._capture_batch_context()
        previous = self._live_summary_result
        if previous is not None and previous is not result:
            self._release_result(previous)

        self._live_summary_result = result
        self._live_summary_active = True
        self._active_result_kind = "live-summary"
        self._last_result = result
        self._result_state = "complete"
        self._result_form_identity = self._live_condition_identity
        self._pending_result_identity = None
        self._restored_encoding_warning = None
        self.query_one("#live-summary-metadata", Static).update(
            self._format_tracking_summary(result.tracking_snapshot)
        )
        result_box = self.query_one("#result-box", RichLog)
        self._set_result(result_box, self._build_result_dashboard(result))
        self._set_result_status("complete", result)
        self._sync_result_context_notice()
        self._set_export_settings_visible(False)
        self.query_one("#export-status", Static).update("")
        self._sync_live_view()
        self._set_live_notice("摘要固定於上述追蹤截止點；再次查看會建立新快照。")

    def _format_tracking_metadata(self, snapshot: TrackingSnapshot | None) -> str:
        if snapshot is None:
            return "資料來源：追蹤快照"
        request = snapshot.conditions
        levels = "不限 Level" if request.levels is None else ", ".join(request.levels)
        start = request.start_dt.isoformat(sep=" ") if request.start_dt else "未設定"
        end = request.end_dt.isoformat(sep=" ") if request.end_dt else "未設定"
        keyword = request.keyword if request.keyword else "未設定"
        gaps = "；".join(snapshot.gaps) if snapshot.gaps else "無"
        pending = "有待完成內容" if snapshot.has_pending else "無"
        return (
            "資料來源：追蹤快照\n"
            f"來源：{request.path}\n"
            f"擷取時間：{snapshot.captured_at.astimezone().isoformat(timespec='seconds')}\n"
            f"截止序號：{snapshot.cutoff}\n"
            f"已完成 Log：{snapshot.completed}；摘要命中：{snapshot.cutoff}\n"
            f"尾端：{pending}\n"
            f"缺口：{gaps}\n"
            f"條件：Level {levels}；關鍵字 {keyword}；忽略大小寫 {request.ignore_case}\n"
            f"時間：{start} 至 {end}\n"
            f"Pattern：{request.log_pattern or '預設 Logback'}"
        )

    def _format_tracking_summary(self, snapshot: TrackingSnapshot | None) -> str:
        if snapshot is None:
            return "資料來源：追蹤快照"
        return f"資料來源：追蹤快照；截止序號：{snapshot.cutoff}"

    def action_show_tracking_details(self) -> None:
        result = self._live_summary_result
        if result is None or result.tracking_snapshot is None:
            return
        self.push_screen(
            LiveSummaryDetailsScreen(
                self._format_tracking_metadata(result.tracking_snapshot)
            )
        )

    def _return_to_live(self) -> None:
        if not self._live_summary_active:
            return
        self._live_summary_active = False
        self._active_result_kind = "live-panel"
        self._last_result = None
        self._result_state = "idle"
        self._result_form_identity = None
        self._pending_result_identity = None
        self._restored_encoding_warning = None
        self._set_export_settings_visible(False)
        self._sync_result_context_notice()
        self._sync_live_view()
        panel = self.query_one("#live-panel", LivePanel)
        panel.refresh_session()
        self._set_live_notice("已返回即時追蹤；事件清單位置與跟隨設定已保留。")

    def _set_live_notice(self, message: str) -> None:
        if not self.is_running:
            return
        self.query_one("#analysis-mode-status", Static).update(message)

    def _sync_live_controls(self) -> None:
        if not self.is_running or not self.is_mounted:
            return
        session = self._live_session
        state = None if session is None else session.state
        task = self._live_operation_task
        live_busy = task is not None and not task.done()
        application_busy = bool(getattr(self, "_operation_in_progress", False))
        tracking = state in {"tracking", "interrupted"}
        self.query_one("#analysis-mode", Select).disabled = (
            live_busy or application_busy or tracking
        )
        self._set_condition_controls_locked(tracking or live_busy)
        self.query_one("#live-panel", LivePanel).set_busy(live_busy)

    def action_cancel_analysis(self) -> None:
        control = self._live_summary_control
        if control is not None:
            if not control.is_cancelled:
                control.cancel()
                if self.is_running:
                    button = self.query_one("#cancel-analysis", Button)
                    button.disabled = True
                    button.label = "取消中…"
                    self._set_live_notice("追蹤摘要取消中，等待暫存工作停止…")
            return
        task = self._live_operation_task
        if (
            self._live_operation_name == "重新分析來源"
            and task is not None
            and not task.done()
        ):
            task.cancel()
            if self.is_running:
                button = self.query_one("#cancel-analysis", Button)
                button.disabled = True
                button.label = "取消中…"
                self._set_live_notice("重新分析取消中，等待候選暫存清理…")
            return
        super().action_cancel_analysis()

    def _set_condition_controls_locked(self, locked: bool) -> None:
        if locked:
            if self._condition_disabled_state is None:
                self._condition_disabled_state = {}
            for widget_id in _LIVE_CONDITION_WIDGET_IDS:
                if widget_id in self._condition_disabled_state:
                    continue
                try:
                    widget = self.query_one(f"#{widget_id}")
                except NoMatches:
                    continue
                self._condition_disabled_state[widget_id] = bool(widget.disabled)
                widget.disabled = True
            return

        previous = self._condition_disabled_state
        if previous is None:
            return
        self._condition_disabled_state = None
        for widget_id, was_disabled in previous.items():
            try:
                self.query_one(f"#{widget_id}").disabled = was_disabled
            except NoMatches:
                continue

    async def _shutdown_live_workflow(  # noqa: PLR0912, PLR0915 - shutdown must complete each owned-resource cleanup step
        self,
    ) -> None:
        pending = self._pending_live_pattern
        self._pending_live_pattern = None
        if pending is not None and not pending.result.done():
            pending.result.set_result(None)
        control = self._live_summary_control
        if control is not None:
            control.cancel()

        operation = self._live_operation_task
        if operation is not None and not operation.done():
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    if operation.done():
                        break
                    # 外層退出等待被取消時，仍等候安全交接與候選暫存清理。
                    continue
                except Exception:  # noqa: BLE001 - continue shutdown after background failure
                    break
            if operation.done():
                try:
                    operation.result()
                except asyncio.CancelledError:
                    logger.info("Live operation was cancelled while the TUI was closing")
                except Exception:
                    logger.exception("Live operation failed while the TUI was closing")

        runner = self._live_runner
        session = self._live_session
        if runner is not None:
            try:
                await runner.stop()
            except asyncio.CancelledError:
                logger.info("Live runner stop completed with cancellation during TUI shutdown")
            except Exception:
                logger.exception("Unable to stop live runner while the TUI was closing")
        if session is not None:
            try:
                session.close()
            except OSError:
                logger.exception("Unable to close live session while the TUI was closing")

        if self._live_pending_snapshot is not None:
            try:
                self._live_pending_snapshot.release()
            except OSError:
                logger.exception("Unable to release pending tracking snapshot")
            self._live_pending_snapshot = None

        active = getattr(self, "_last_result", None)
        hidden_results = (self._batch_result, self._live_summary_result)
        released_ids: set[int] = set()
        for result in hidden_results:
            if result is None or result is active or id(result) in released_ids:
                continue
            released_ids.add(id(result))
            self._release_result(result, close_browser=False)
        self._batch_result = None
        self._live_summary_result = None
        self._live_summary_active = False
