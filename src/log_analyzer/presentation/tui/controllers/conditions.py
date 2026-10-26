from pathlib import Path

from textual.widgets import Checkbox, Input

from log_analyzer.presentation.tui.components.condition_overview import (
    ConditionItem,
    ConditionsOverview,
)


class ConditionsControllerMixin:
    """將目前表單投影成條件總覽；重設只影響下一次分析。"""

    def _refresh_condition_overview(self) -> None:
        """從現有表單及來源快取更新總覽，不重新掃描或解析日誌。

        Returns:
            None。
        """
        if not self.is_mounted:
            return

        def value(key: str) -> str:
            return self.query_one(f"#{key}", Input).value.strip()

        state = self._file_selection_state()
        source = getattr(self, "_condition_source", None)
        files = "無法讀取來源"
        if source is not None:
            total = (
                len(state.available.filenames)
                if state.available
                else len(source.filenames)
            )
            files = (
                f"{len(source.filenames)} / {total} 個"
                if state.selected_files is not None
                else f"全部 {len(source.filenames)} 個"
            )
        unrestricted = self.query_one("#level_unrestricted", Checkbox).value
        levels = (
            "不限"
            if unrestricted
            else "、".join(self._collect_checked_levels()) or "未選擇（無法分析）"
        )
        start = " ".join(filter(None, (value("start_date"), value("start_time"))))
        end = " ".join(filter(None, (value("end_date"), value("end_time"))))
        time = (
            f"{start or '不限起點'} ～ {end or '不限終點'}" if start or end else "不限"
        )
        keyword = value("keyword")
        if keyword:
            keyword += (
                "（忽略大小寫）"
                if self.query_one("#ignore_case", Checkbox).value
                else "（區分大小寫）"
            )
        manual = self.query_one("#manual_pattern_override", Checkbox).value
        xml = value("logback_xml_path")
        recipe_default = bool(getattr(self, "_recipe_autofill_source", ""))
        if manual:
            pattern = "手動覆寫" if value("log_pattern") else "手動覆寫（尚未輸入）"
        elif xml:
            prefix = (
                "自動 XML"
                if xml == getattr(self, "_auto_logback_xml_path", "")
                else "指定 XML"
            )
            pattern = f"{prefix}：{Path(xml).name}"
        else:
            pattern = "內建 Logback（配方）" if recipe_default else "自動偵測 XML／內建"
        explicit_files = (
            state.selected_files is not None
            and state.path == self._normalize_recipe_source(value("path") or ".")
        )
        self.query_one("#condition-overview", ConditionsOverview).set_items(
            [
                ConditionItem("files", "檔案", files, explicit_files, "全選"),
                ConditionItem("levels", "Level", levels, not unrestricted),
                ConditionItem("time", "時間", time, bool(start or end)),
                ConditionItem(
                    "keyword", "關鍵字", keyword or "不限", bool(value("keyword"))
                ),
                ConditionItem(
                    "pattern",
                    "Pattern",
                    pattern,
                    manual
                    or bool(xml and xml != getattr(self, "_auto_logback_xml_path", ""))
                    or recipe_default,
                    "恢復自動",
                ),
            ]
        )

    def on_conditions_overview_clear_requested(
        self, event: ConditionsOverview.ClearRequested
    ) -> None:
        """重設指定表單條件，保留分析結果且不自動開始分析。

        Args:
            event: 總覽元件送出的重設項目。

        Returns:
            None。
        """
        event.stop()
        if event.key == "keyword":
            self.query_one("#keyword", Input).value = ""
        elif event.key == "time":
            for key in ("start_date", "start_time", "end_date", "end_time"):
                self.query_one(f"#{key}", Input).value = ""
        elif event.key == "levels":
            self.query_one("#level_unrestricted", Checkbox).value = True
        elif event.key == "files":
            self._file_selection_state().selected_files = None
            path = self.query_one("#path", Input).value.strip() or "."
            self.update_path_preview(path)
            self._last_logback_autofill_source = ""
            self._autofill_logback_settings(path)
        elif event.key == "pattern":
            self.query_one("#manual_pattern_override", Checkbox).value = False
            self.query_one("#log_pattern", Input).value = ""
            self.query_one("#logback_xml_path", Input).value = ""
            self._analysis_log_pattern = None
            self._auto_logback_xml_path = ""
            self._auto_logback_xml_paths = ()
            self._last_logback_autofill_source = ""
            self._recipe_autofill_source = ""
            self._sync_pattern_preview_for_current_selection()
            self._autofill_logback_settings(
                self.query_one("#path", Input).value.strip() or "."
            )
        self.call_after_refresh(self._refresh_condition_overview)
        self.call_after_refresh(self._sync_result_context_notice)
