from __future__ import annotations
import asyncio
import logging
from dataclasses import replace
from functools import partial
from textual.containers import Container
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static
from log_analyzer.application.analysis_service import (
    AnalysisResult,
    ResultExportOptions,
    build_output_path,
    export_analysis_bundle,
    export_analysis_result,
)
from log_analyzer.infrastructure.exporting.fields import EXPORT_FIELDS
from log_analyzer.infrastructure.exporting import ExportConflictError
from log_analyzer.presentation.tui.screens.export_conflict import ExportConflictScreen
from log_analyzer.presentation.tui.recent_state import load_recent_tui_state

logger = logging.getLogger(__name__)


def _export_reports_and_manifest(
    result: AnalysisResult,
    options: ResultExportOptions,
    include_manifest: bool,
) -> tuple[list[str], str | None]:
    if include_manifest:
        return export_analysis_bundle(result, options)
    return export_analysis_result(result, options), None


class ExportWorkflowMixin:
    def action_show_export_settings(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        if self._last_result is None or self._result_state != "complete":
            return

        self._set_export_settings_visible(True)
        self._sync_result_context_notice()
        self.call_after_refresh(self._reveal_export_confirmation)

    def _reveal_export_confirmation(self) -> None:
        confirm_button = self.query_one("#confirm-export", Button)
        confirm_button.scroll_visible(animate=False)
        confirm_button.focus()

    def _set_export_controls_disabled(self, disabled: bool) -> None:
        for selector in ("#run", "#clear", "#export-result", "#confirm-export"):
            self.query_one(selector, Button).disabled = disabled

    def _capture_export_request(self) -> tuple[ResultExportOptions, bool, str]:
        selected_fields = tuple(
            field
            for field in EXPORT_FIELDS
            if self.query_one(f"#export_field_{field}", Checkbox).value
        )
        if not selected_fields:
            raise ValueError("請至少選擇一個輸出欄位")

        output_path = self.query_one("#output_path", Input).value.strip() or "."
        output_name = self.query_one("#output_name", Input).value
        fmt = str(self.query_one("#format", Select).value or "csv")
        split_by_level = self.query_one("#split_by_level", Checkbox).value
        include_manifest = self.query_one("#include_manifest", Checkbox).value
        recent_state = load_recent_tui_state() or {}
        recent_state.update(
            {
                "output_path": output_path,
                "format": fmt,
                "split_by_level": split_by_level,
            }
        )
        self._save_recent_form_state(recent_state)
        options = ResultExportOptions(
            output_path=build_output_path(output_path, output_name, fmt),
            fmt=fmt,
            fields=None if selected_fields == EXPORT_FIELDS else selected_fields,
            split_by_level=split_by_level,
        )
        return options, include_manifest, output_name

    def _display_export_success(
        self,
        result: AnalysisResult,
        export_outcome: tuple[list[str], str | None],
        output_name: str,
        export_status: Static,
    ) -> None:
        exported_files, manifest_path = export_outcome
        result_box = self.query_one("#result-box", RichLog)
        self._set_result(
            result_box,
            self._build_result_dashboard(result),
        )
        report_paths = ", ".join(exported_files) if exported_files else "完成"
        if manifest_path is not None:
            export_status.update(f"報表：{report_paths}\n證據清單：{manifest_path}")
        else:
            export_status.update(f"匯出完成：{report_paths}")
        self._refresh_output_name_default(output_name)

    async def action_confirm_export(self) -> None:
        result = self._last_result
        if (
            getattr(self, "_operation_in_progress", False)
            or result is None
            or self._result_state != "complete"
        ):
            return

        export_status = self.query_one("#export-status", Static)
        self._operation_in_progress = True
        self._set_export_controls_disabled(True)
        export_status.update("匯出中…")

        try:
            options, include_manifest, output_name = self._capture_export_request()
        except Exception as exc:  # noqa: BLE001 - export failures must preserve the analyzed result
            logger.warning("TUI export failed: %s", exc)
            if self.is_running:
                export_status.update(f"匯出失敗：{exc}")
            self._finish_export_operation()
            return

        await self._run_frozen_export(result, options, include_manifest, output_name)

    async def _run_frozen_export(
        self,
        result: AnalysisResult,
        options: ResultExportOptions,
        include_manifest: bool,
        output_name: str,
    ) -> None:
        keep_operation_open = False
        try:
            loop = asyncio.get_running_loop()
            export_outcome = await loop.run_in_executor(
                None,
                partial(
                    _export_reports_and_manifest, result, options, include_manifest
                ),
            )
            if self.is_running and self._last_result is result:
                self._display_export_success(
                    result,
                    export_outcome,
                    output_name,
                    self.query_one("#export-status", Static),
                )
        except ExportConflictError as exc:
            logger.info("TUI export conflicts with existing paths: %s", exc.paths)
            if (
                self.is_running
                and self._last_result is result
                and self._result_state == "complete"
            ):
                self._pending_export_conflict = (
                    result,
                    options,
                    include_manifest,
                    output_name,
                )
                self.push_screen(
                    ExportConflictScreen(exc.paths), self._on_export_conflict_choice
                )
                keep_operation_open = True
        except Exception as exc:  # noqa: BLE001 - export failures must preserve the analyzed result
            logger.warning("TUI export failed: %s", exc)
            if self.is_running and self._last_result is result:
                self.query_one("#export-status", Static).update(f"匯出失敗：{exc}")
        finally:
            if not keep_operation_open:
                self._finish_export_operation()

    def _on_export_conflict_choice(self, choice: str | None) -> None:
        pending = getattr(self, "_pending_export_conflict", None)
        self._pending_export_conflict = None
        if pending is None:
            self._finish_export_operation()
            return

        result, options, include_manifest, output_name = pending
        if (
            choice == "overwrite"
            and self.is_running
            and self._last_result is result
            and self._result_state == "complete"
        ):
            asyncio.create_task(
                self._run_frozen_export(
                    result,
                    replace(options, overwrite=True),
                    include_manifest,
                    output_name,
                )
            )
            return

        if self.is_running:
            status = self.query_one("#export-status", Static)
            if choice == "rename":
                status.update("請修改輸出檔名後重新匯出")
                output_name_input = self.query_one("#output_name", Input)
                output_name_input.focus()
                output_name_input.select_all()
            else:
                status.update("匯出已取消")
        self._finish_export_operation()

    def _finish_export_operation(self) -> None:
        self._operation_in_progress = False
        if self.is_running:
            self._set_export_controls_disabled(False)
        if getattr(self, "_release_result_when_operation_finishes", False):
            self._release_result_when_operation_finishes = False
            self._release_last_result(close_browser=False)

    def _set_export_settings_visible(self, visible: bool) -> None:
        self.query_one("#export-settings", Container).display = visible

    def _focused_inside_export_settings(self) -> bool:
        widget = self.focused
        while widget is not None:
            if widget.id == "export-settings":
                return True
            widget = widget.parent
        return False
