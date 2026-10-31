from __future__ import annotations
import logging
import os
from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.containers import Container
from textual.widgets import Button, Checkbox, Input, RichLog, Static
from log_analyzer.application.analysis.service import AnalysisResult
from log_analyzer.presentation.tui.screens.event_browser import EventBrowserScreen
from log_analyzer.presentation.tui.views import build_dashboard_view, build_idle_view, analyze_dashboard
from log_analyzer.presentation.dashboard_navigation import build_dashboard_targets
from log_analyzer.presentation.tui.screens.summary_navigation import SummaryNavigationScreen
from log_analyzer.presentation.analysis_labels import export_result_context

logger = logging.getLogger(__name__)


def _normal_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(value.strip() or ".")))


class ResultStateMixin:
    def _capture_result_form_identity(self) -> tuple:
        form = dict(self._capture_recipe_state())
        form.pop("display_mode", None)
        form["path"] = _normal_path(str(form.get("path", ".")))
        if form.get("pattern_mode") == "custom":
            form.pop("logback_xml_path", None)
        else:
            form.pop("log_pattern", None)
            xml = str(form.get("logback_xml_path", "")).strip()
            form["logback_xml_path"] = _normal_path(xml) if xml else ""
        if form.get("level_unrestricted"):
            form.pop("levels", None)
        elif "levels" in form:
            form["levels"] = tuple(form["levels"])
        state = self._file_selection_state()
        form["selected_files"] = (
            state.selected_files if _normal_path(state.path) == form["path"] else None
        )
        return tuple(sorted(form.items()))

    def _remember_result_context(self, result) -> None:
        self._result_form_identity = getattr(self, "_pending_result_identity", None)
        if (
            self._result_form_identity is None
            and getattr(result, "analysis_request", None) is not None
        ):
            self._result_form_identity = self._capture_result_form_identity()
        self._sync_result_context_notice()

    def _sync_result_context_notice(self) -> None:
        if not self.is_mounted:
            return
        notice = next(iter(self.query("#result-context-notice")), None)
        if notice is None or not notice.is_mounted:
            return
        result = getattr(self, "_last_result", None)
        identity = getattr(self, "_result_form_identity", None)
        dirty = (
            result is not None
            and identity is not None
            and identity != self._capture_result_form_identity()
        )
        notice.display = dirty
        notice.update(
            Text("條件已修改，尚未重新分析；畫面與匯出仍使用上次成功結果。")
            if dirty
            else Text()
        )
        if result is not None:
            self.query_one("#export-result-context", Static).update(
                Text(export_result_context(result))
            )

    def _refresh_dashboard_after_resize(self) -> None:
        result = getattr(self, "_last_result", None)
        if result is None or not self.is_running:
            return

        result_box = self.query_one("#result-box", RichLog)
        compact = self._should_compact_dashboard(self.size.width, self.size.height)
        self._set_result(
            result_box, self._build_dashboard_with_restore_warning(result, compact)
        )

    def _build_dashboard_with_restore_warning(
        self, result: AnalysisResult, compact: bool
    ) -> Group:
        dashboard = self._interactive_dashboard(result, compact)
        if not self._restored_encoding_warning:
            return dashboard
        return Group(
            Panel(
                Text(self._restored_encoding_warning, style="yellow"),
                title="本次掃描編碼提醒",
                border_style="yellow",
                padding=(0, 1),
                expand=True,
            ),
            dashboard,
        )

    def _build_result_dashboard(self, result: AnalysisResult):
        return self._interactive_dashboard(
            result, self._should_compact_dashboard(self.size.width, self.size.height)
        )

    def _display_analysis_result(
        self, result_box: RichLog, result: AnalysisResult
    ) -> None:
        if not self.is_running:
            self._release_result(result, close_browser=False)
            return
        self._restored_encoding_warning = None
        previous_result = self._last_result
        self._last_result = result
        if previous_result is not None and previous_result is not result:
            self._release_result(previous_result)
        self._set_result(
            result_box,
            self._build_result_dashboard(result),
        )
        self._set_result_status("complete", result)
        self._remember_result_context(result)

    def action_clear_result(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        if self._last_result is None and self._result_state != "no_results":
            return
        result_box = self.query_one("#result-box", RichLog)
        self._restored_encoding_warning = None
        self._release_last_result()
        self._set_export_settings_visible(False)
        self.query_one("#export-status", Static).update("")
        self._set_result(result_box, build_idle_view())
        self._set_result_status("idle")

    def action_browse_events(self) -> None:
        result = self._last_result
        if result is None:
            return
        self.push_screen(EventBrowserScreen(result))

    def _prepare_dashboard_navigation(self, result) -> int:
        """同一結果重繪時沿用入口，換結果才建立新的世代。

        Args:
            result: 目前保留的分析結果。

        Returns:
            此結果的世代編號，用於拒絕舊畫面的殘留點擊。
        """
        if getattr(self, "_dashboard_target_result", None) is not result:
            targets = build_dashboard_targets(
                result, analyze_dashboard(result)
            )
            self._dashboard_targets = targets
            self._dashboard_navigation_generation = (
                getattr(self, "_dashboard_navigation_generation", 0) + 1
            )
            self._dashboard_target_result = result
        return self._dashboard_navigation_generation

    def _interactive_dashboard(self, result, compact: bool):
        generation = self._prepare_dashboard_navigation(result)
        return build_dashboard_view(result, compact, navigation_generation=generation)

    def action_browse_summary(self) -> None:
        """以鍵盤選擇目前保留結果的摘要入口。

        Returns:
            None；沒有結果或有進行中的操作時不開啟導覽。
        """
        result = self._last_result
        if (
            result is None
            or getattr(self, "_operation_in_progress", False)
            or self.screen is not self.screen_stack[0]
        ):
            return
        generation = self._prepare_dashboard_navigation(result)
        if not self._dashboard_targets:
            return

        def selected(index: int | None) -> None:
            if index is not None:
                self.action_open_dashboard_target(generation, index)

        self.push_screen(SummaryNavigationScreen(self._dashboard_targets), selected)

    def action_open_dashboard_target(self, generation: int, index: int) -> None:
        """開啟摘要對應的精確瀏覽範圍，忽略舊結果殘留的點擊。

        Args:
            generation: 產生入口時的結果世代。
            index: 該世代的入口索引。

        Returns:
            None；不重新分析、不變更表單與匯出設定。
        """
        result = self._last_result
        if (
            result is None
            or getattr(self, "_operation_in_progress", False)
            or getattr(self, "_dashboard_target_result", None) is not result
            or generation != getattr(self, "_dashboard_navigation_generation", None)
            or not 0 <= index < len(self._dashboard_targets)
        ):
            return
        target = self._dashboard_targets[index]
        self.push_screen(EventBrowserScreen(
            result, initial_level=target.level, initial_group_index=target.group_index
        ))

    def _release_last_result(self, close_browser: bool = True) -> None:
        result = getattr(self, "_last_result", None)
        self._last_result = None
        self._dashboard_target_result = None
        self._dashboard_targets = ()
        self._result_form_identity = None
        self._sync_result_context_notice()
        if result is None:
            return

        self._release_result(result, close_browser=close_browser)

    def _release_result(
        self, result: AnalysisResult, close_browser: bool = True
    ) -> None:
        self._dismiss_event_browser_for_result(result, close_browser=close_browser)
        matched_logs = getattr(result, "matched_logs", None)
        release = getattr(matched_logs, "release", None)
        if callable(release):
            try:
                release()
            except Exception:
                logger.exception("釋放分析結果暫存資料失敗")

    def _dismiss_event_browser_for_result(
        self,
        result: AnalysisResult,
        *,
        close_browser: bool = True,
    ) -> None:
        try:
            active_screen = self.screen
        except Exception:  # noqa: BLE001 - app may be shutting down without an active screen
            active_screen = None
        if (
            isinstance(active_screen, EventBrowserScreen)
            and active_screen.result is result
        ):
            active_screen.invalidate()
            if close_browser and active_screen.is_mounted:
                active_screen.dismiss()

    def _restore_previous_result(
        self,
        result_box: RichLog,
        notice: str,
        detail: str | None = None,
        *,
        encoding_warning: str | None = None,
    ) -> bool:
        previous_result = self._last_result
        if previous_result is None or not self.is_running:
            return False

        self._restored_encoding_warning = encoding_warning
        self._set_result(
            result_box,
            self._build_dashboard_with_restore_warning(
                previous_result,
                self._should_compact_dashboard(self.size.width, self.size.height),
            ),
        )
        self._set_result_status("complete", previous_result)
        status_text = notice if not detail else f"{notice}：{detail}"
        self.query_one("#result-status", Static).update(Text(status_text))
        return True

    def _should_compact_dashboard(self, width: int, height: int) -> bool:
        return self._is_compact(width, height)

    def _set_result(self, result_box: RichLog, renderable: object) -> None:
        result_box.clear()
        content_region = getattr(result_box, "scrollable_content_region", None)
        width = getattr(content_region, "width", None)
        result_box.write(
            renderable, width=width if width and width > 0 else None, expand=True
        )
        result_box.scroll_home()

    def _set_result_status(
        self, state: str, result: AnalysisResult | None = None
    ) -> None:
        status = self.query_one("#result-status", Static)
        clear_button = self.query_one("#clear", Button)
        export_button = self.query_one("#export-result", Button)
        confirm_button = self.query_one("#confirm-export", Button)
        self._result_state = state
        has_result = self._last_result is not None
        self.query_one("#content").set_class(has_result, "has-result")
        self.query_one("#shell").set_class(has_result, "has-result")
        can_export = state == "complete" and result is not None
        export_button.disabled = not can_export
        confirm_button.disabled = not can_export
        retry_actions = self.query_one("#zero-result-actions", Container)
        if state == "no_results":
            retry_actions.remove_class("zero-results-hidden")
        else:
            retry_actions.add_class("zero-results-hidden")

        if state == "complete" and result is not None:
            status.update(
                f"狀態：完成｜總 Log {result.total_logs}｜命中 {result.matched_occurrences}"
            )
            clear_button.disabled = False
            self.query_one("#browse-events", Button).disabled = False
            for button_id in (
                "retry_without_time",
                "retry_without_level",
                "retry_without_keyword",
            ):
                self.query_one(f"#{button_id}", Button).disabled = True
            return

        if state == "no_results":
            status.update("狀態：沒有符合資料")
            clear_button.disabled = False
            self.query_one("#browse-events", Button).disabled = True
            has_time_filter = any(
                self.query_one(f"#{field_id}", Input).value.strip()
                for field_id in ("start_date", "start_time", "end_date", "end_time")
            )
            has_level_filter = not self.query_one("#level_unrestricted", Checkbox).value
            has_keyword_filter = bool(self.query_one("#keyword", Input).value.strip())
            self.query_one("#retry_without_time", Button).disabled = not has_time_filter
            self.query_one(
                "#retry_without_level", Button
            ).disabled = not has_level_filter
            self.query_one(
                "#retry_without_keyword", Button
            ).disabled = not has_keyword_filter
            return

        messages = {
            "idle": "狀態：尚未分析",
            "running": "狀態：分析中",
            "failed": "狀態：失敗",
            "cancelled": "狀態：已取消",
        }
        status.update(messages[state])
        clear_button.disabled = True
        self.query_one("#browse-events", Button).disabled = True
        for button_id in (
            "retry_without_time",
            "retry_without_level",
            "retry_without_keyword",
        ):
            self.query_one(f"#{button_id}", Button).disabled = True
