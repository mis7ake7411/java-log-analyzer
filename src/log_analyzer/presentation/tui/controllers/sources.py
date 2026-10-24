from __future__ import annotations
import logging
from textual.widgets import Input, Static
from log_analyzer.domain.log_sources import LogSourceSelection, select_log_source
from log_analyzer.presentation.tui.screens.path_picker import (
    DirectoryPickerScreen,
    FilePickerScreen,
    SourcePickerScreen,
)
from log_analyzer.presentation.tui.components.source_scope import SourceScopePreview
from log_analyzer.presentation.tui.views import format_path_status
from log_analyzer.infrastructure.paths import (
    inspect_directory_path,
    inspect_file_path,
    validate_pasted_path,
)
from log_analyzer.application.source_file_selection import SourceFileSelection
from log_analyzer.presentation.tui.screens.file_selection import (
    SourceFileSelectionScreen,
)

logger = logging.getLogger(__name__)


class SourceControllerMixin:
    def _file_selection_state(self) -> SourceFileSelection:
        state = getattr(self, "_source_file_selection", None)
        if state is None:
            state = self._source_file_selection = SourceFileSelection()
        return state

    def _resolve_log_source(self, path: str) -> LogSourceSelection:
        return self._file_selection_state().resolve(path)

    def action_select_source_files(self) -> None:
        if getattr(self, "_operation_in_progress", False):
            return
        path = self.query_one("#path", Input).value.strip() or "."
        try:
            source = select_log_source(path)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error")
            return
        state = self._file_selection_state()
        selected = state.selected_files if state.path == source.path else None

        def apply_selection(filenames: tuple[str, ...] | None) -> None:
            if filenames is None:
                return
            try:
                state.choose(source, filenames)
                self._refresh_source_scope_preview(source.path)
                self.call_after_refresh(self._sync_result_context_notice)
                self._last_logback_autofill_source = ""
                self._autofill_logback_settings(source.path, selected_files=filenames)
                xml_path = self.query_one("#logback_xml_path", Input).value.strip()
                if xml_path and not self.query_one("#manual_pattern_override").value:
                    self.action_load_logback_xml()
            except (OSError, ValueError) as exc:
                self.notify(str(exc), severity="error")

        self.push_screen(
            SourceFileSelectionScreen(
                source, selected if selected is not None else source.filenames
            ),
            apply_selection,
        )

    def handle_path_paste(self, path_input: Input, pasted_text: str) -> bool:
        """驗證並套用路徑欄位的貼上內容；回傳是否已處理事件。"""
        if path_input.id not in {"path", "output_path", "logback_xml_path"}:
            return False

        is_valid, normalized_path, message = validate_pasted_path(
            pasted_text, path_input.id
        )
        if not is_valid:
            self._update_pasted_path_status(path_input.id, message, "red")
            if path_input.id == "path":
                self._clear_source_scope_preview(message)
            return True

        path_input.value = normalized_path
        self._update_pasted_path_status(path_input.id, message, "green")
        if path_input.id == "path":
            self.update_path_preview(normalized_path)
            self._autofill_logback_settings(normalized_path)
        elif path_input.id == "logback_xml_path":
            self._auto_logback_xml_path = ""
            self._auto_logback_xml_paths = ()
            self._last_logback_autofill_source = ""
            self._recipe_autofill_source = ""
            self._sync_pattern_preview_for_current_selection()
        return True

    def _update_pasted_path_status(
        self, field_id: str, message: str, color: str
    ) -> None:
        if field_id == "path":
            self.query_one("#path-status", Static).update(
                format_path_status(message, color, require_writable=False)
            )
            return
        if field_id == "output_path":
            self.query_one("#output-path-status", Static).update(
                format_path_status(message, color, require_writable=True)
            )
            return
        self.query_one("#logback-xml-status", Static).update(
            f"[{color}]{message}[/{color}]"
        )

    def update_path_preview(
        self, path: str, status_id: str = "#path-status", require_writable: bool = False
    ) -> None:
        if status_id == "#path-status":
            self._refresh_source_scope_preview(path)
            return
        preview = self.query_one(status_id, Static)
        # 狀態列即時反映路徑可用性，避免要按執行才發現權限或拼字錯誤
        color, message, _, _ = inspect_directory_path(
            path, require_writable=require_writable
        )
        preview.update(
            format_path_status(message, color, require_writable=require_writable)
        )

    def _refresh_source_scope_preview(self, path: str) -> LogSourceSelection | None:
        try:
            source = self._resolve_log_source(path.strip() or ".")
        except (OSError, ValueError) as exc:
            self._clear_source_scope_preview(str(exc))
            return None
        self._show_source_scope(source)
        return source

    def _show_source_scope(self, source: LogSourceSelection) -> None:
        if source.is_file:
            color, message, is_valid, _ = inspect_file_path(source.path)
        else:
            color, message, is_valid, _ = inspect_directory_path(source.path)

        if not is_valid:
            self._clear_source_scope_preview(message)
            return

        self.query_one("#path-status", Static).update(
            format_path_status(message, color, False)
        )
        self.query_one("#source-scope-preview", SourceScopePreview).show_source(source)
        state = self._file_selection_state()
        if state.selected_files is not None and state.available is not None:
            self.query_one(
                "#source-scope-preview", SourceScopePreview
            ).show_selection_count(len(state.available.filenames))

    def _clear_source_scope_preview(self, message: str) -> None:
        self.query_one("#path-status", Static).update(
            format_path_status(message, "red", False)
        )
        self.query_one("#source-scope-preview", SourceScopePreview).show_invalid(
            message
        )

    def action_browse_path(self) -> None:
        self._open_directory_picker(
            target_id="path",
            title="\u9078\u64c7 Log \u4f86\u6e90",
            hint="可用樹狀瀏覽，或直接手動輸入路徑",
            confirm_label="使用此路徑",
            require_writable=False,
        )

    def action_browse_output_path(self) -> None:
        self._open_directory_picker(
            target_id="output_path",
            title="選擇目標資料夾",
            hint="可用樹狀瀏覽，或直接手動輸入路徑",
            confirm_label="使用此資料夾",
            require_writable=True,
        )

    def action_browse_logback_xml(self) -> None:
        current_path = self.query_one("#logback_xml_path", Input).value.strip() or ""
        self.push_screen(
            FilePickerScreen(
                current_path,
                "選擇 Logback XML",
                "請選擇 logback.xml 或 logback-spring.xml",
                "載入",
            ),
            callback=self._apply_and_load_logback_xml,
        )

    def _open_directory_picker(
        self,
        target_id: str,
        title: str,
        hint: str,
        confirm_label: str,
        require_writable: bool,
    ) -> None:
        current_path = self.query_one(f"#{target_id}", Input).value.strip() or "."
        # 瀏覽成功後直接回填到對應欄位，手動輸入仍保留為 fallback
        if target_id == "path":
            picker = SourcePickerScreen(current_path, title, hint, confirm_label)
        else:
            picker = DirectoryPickerScreen(
                current_path,
                title,
                hint,
                confirm_label,
                require_writable=require_writable,
            )
        self.push_screen(
            picker,
            callback=lambda selected_path: self._apply_selected_directory(
                target_id, selected_path
            ),
        )

    def _apply_selected_directory(
        self, target_id: str, selected_path: str | None
    ) -> None:
        if not selected_path:
            return

        path_input = self.query_one(f"#{target_id}", Input)
        path_input.value = selected_path
        path_input.focus()
        self.update_path_preview(
            selected_path,
            "#output-path-status" if target_id == "output_path" else "#path-status",
            require_writable=target_id == "output_path",
        )
        if target_id == "path":
            self._autofill_logback_settings(selected_path)

    def _apply_selected_logback_xml(self, selected_path: str | None) -> None:
        if not selected_path:
            return

        self._auto_logback_xml_path = ""
        self._auto_logback_xml_paths = ()
        self._last_logback_autofill_source = ""
        xml_input = self.query_one("#logback_xml_path", Input)
        xml_input.value = selected_path
        self._recipe_autofill_source = ""
        self._sync_pattern_preview_for_current_selection()
        xml_input.focus()

    def _apply_and_load_logback_xml(self, selected_path: str | None) -> None:
        self._apply_selected_logback_xml(selected_path)
        if selected_path:
            self.action_load_logback_xml()
