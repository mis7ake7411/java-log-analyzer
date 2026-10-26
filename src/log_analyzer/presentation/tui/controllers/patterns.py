from __future__ import annotations
import logging
import os
from pathlib import Path
from xml.etree.ElementTree import ParseError
from textual.containers import Container
from textual.widgets import Checkbox, Collapsible, Input, Static
from log_analyzer.domain.log_sources import LogSourceSelection, select_log_source
from log_analyzer.domain.logback_pattern import DEFAULT_LOGBACK_PATTERN
from log_analyzer.domain.logback_xml import (
    LogbackPatternAssessment,
    LogbackPatternCandidate,
    assess_logback_patterns,
    find_best_logback_pattern,
)

logger = logging.getLogger(__name__)


class PatternSetupMixin:
    def action_preview_format(self) -> None:
        """以目前來源及格式設定開啟唯讀抽樣預覽。

        Returns:
            None；來源無效時以通知呈現原因。
        """
        from log_analyzer.presentation.tui.screens.format_preview import FormatPreviewScreen

        if getattr(self, "_operation_in_progress", False):
            self.notify("分析或匯出完成後，可再預覽格式。")
            return
        try:
            source = self._resolve_log_source(
                self.query_one("#path", Input).value.strip() or "."
            )
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error")
            return
        manual = self.query_one("#manual_pattern_override", Checkbox).value
        xml = self.query_one("#logback_xml_path", Input).value.strip()
        xml_paths = (xml,) if xml else ()
        if (
            not manual
            and (not xml or xml == getattr(self, "_auto_logback_xml_path", ""))
            and not getattr(self, "_recipe_autofill_source", "")
        ):
            xml_paths = tuple(self._find_logback_xml_candidates(source.root))
        self.push_screen(
            FormatPreviewScreen(
                source,
                manual_pattern=self.query_one("#log_pattern", Input).value,
                manual_override=manual,
                xml_paths=xml_paths,
            )
        )

    def _assess_logback_pattern_for_analysis(
        self,
        source: LogSourceSelection,
    ) -> LogbackPatternAssessment | None:
        status = self.query_one("#logback-xml-status", Static)
        xml_path = self.query_one("#logback_xml_path", Input).value.strip()
        if not xml_path:
            self._analysis_log_pattern = None
            status.update("")
            self._set_pattern_preview(
                "預設 Logback",
                DEFAULT_LOGBACK_PATTERN,
                seed_pattern=DEFAULT_LOGBACK_PATTERN,
            )
            return None

        auto_xml_path = getattr(self, "_auto_logback_xml_path", "")
        if xml_path == auto_xml_path and auto_xml_path:
            discovered_paths = self._find_logback_xml_candidates(source.root)
            self._auto_logback_xml_paths = tuple(discovered_paths)
            if not discovered_paths:
                self._auto_logback_xml_path = ""
                self.query_one("#logback_xml_path", Input).value = ""
                self._analysis_log_pattern = None
                status.update("")
                self._set_pattern_preview(
                    "預設 Logback",
                    DEFAULT_LOGBACK_PATTERN,
                    seed_pattern=DEFAULT_LOGBACK_PATTERN,
                )
                return None
            if auto_xml_path not in discovered_paths:
                auto_xml_path = discovered_paths[0]
                self._auto_logback_xml_path = auto_xml_path
                self.query_one("#logback_xml_path", Input).value = auto_xml_path
            xml_paths = tuple(discovered_paths)
        else:
            xml_paths = (xml_path,)
        try:
            assessment = assess_logback_patterns(
                xml_paths,
                source.root,
                selected_files=source.filenames,
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"無法評估 Logback XML：{exc}") from exc

        if assessment.status == "ready" and assessment.selected is not None:
            candidate = assessment.selected
            self._analysis_log_pattern = candidate.pattern
            self._set_pattern_preview(
                f"XML（{Path(candidate.xml_path).name}）",
                candidate.pattern,
                seed_pattern=candidate.pattern,
            )
            status.update(
                f"已自動選用 {candidate.name}，命中 {candidate.matches}/{candidate.checked}"
            )
            return assessment

        self._analysis_log_pattern = None
        status.update(assessment.reason)
        self._set_pattern_preview(
            f"XML（{len(xml_paths)} 份）"
            if len(xml_paths) > 1
            else f"XML（{Path(xml_path).name}）",
            assessment.reason,
            seed_pattern=None,
        )
        if assessment.status == "unsupported":
            raise ValueError(
                f"{assessment.reason}請檢查 XML，或啟用手動覆寫指定 Pattern。"
            )
        return assessment

    def action_load_logback_xml(self) -> None:
        status = self.query_one("#logback-xml-status", Static)
        xml_path = self.query_one("#logback_xml_path", Input).value.strip()
        if not xml_path:
            status.update("請輸入 logback.xml / logback-spring.xml 路徑")
            self._sync_pattern_preview_for_current_selection()
            return

        if self.query_one("#manual_pattern_override", Checkbox).value:
            status.update("手動覆寫已啟用；分析時會使用手動 Pattern。")
            return

        log_dir = self.query_one("#path", Input).value.strip() or "."
        try:
            best_pattern = self._read_selected_logback_pattern(xml_path, log_dir)
        except ValueError as exc:
            status.update(str(exc))
            self._set_pattern_preview(
                f"XML（{Path(xml_path).name}）",
                f"無可用 Pattern：{exc}",
                seed_pattern=None,
            )
            return

        self._set_pattern_preview(
            f"XML（{Path(xml_path).name}）",
            best_pattern.pattern,
            seed_pattern=best_pattern.pattern,
        )
        status.update(
            f"已載入 {best_pattern.name}，命中 {best_pattern.matches}/{best_pattern.checked}"
        )

    def _read_selected_logback_pattern(
        self,
        xml_path: str,
        log_dir: str,
        selected_files: tuple[str, ...] | None = None,
    ) -> LogbackPatternCandidate:
        if not os.path.isfile(xml_path):
            raise ValueError(f"找不到檔案：{os.path.abspath(xml_path)}")

        try:
            source = (
                self._resolve_log_source(log_dir)
                if selected_files is None
                else select_log_source(log_dir, selected_files=selected_files)
            )
            assessment = assess_logback_patterns(
                (xml_path,),
                source.root,
                selected_files=source.filenames,
            )
        except OSError as exc:
            raise ValueError(f"無法讀取 Logback XML：{exc}") from exc

        if assessment.status == "ready" and assessment.selected is not None:
            return assessment.selected
        raise ValueError(assessment.reason)

    def _load_selected_logback_pattern_for_analysis(
        self,
        log_dir: str,
        selected_files: tuple[str, ...] | None = None,
    ) -> str | None:
        self._analysis_log_pattern = None
        manual_override = self.query_one("#manual_pattern_override", Checkbox).value
        pattern_input = self.query_one("#log_pattern", Input)
        status = self.query_one("#logback-xml-status", Static)
        xml_path = self.query_one("#logback_xml_path", Input).value.strip()

        if manual_override:
            pattern = pattern_input.value.strip()
            if not pattern:
                message = "手動 Pattern 不可為空"
                self._set_pattern_preview("手動覆寫", message, seed_pattern=None)
                raise ValueError(message)
            self._analysis_log_pattern = pattern
            self._set_pattern_preview("手動覆寫", pattern, seed_pattern=pattern)
            return pattern

        if not xml_path:
            self._analysis_log_pattern = None
            status.update("")
            self._set_pattern_preview(
                "預設 Logback",
                DEFAULT_LOGBACK_PATTERN,
                seed_pattern=DEFAULT_LOGBACK_PATTERN,
            )
            return None

        try:
            best_pattern = self._read_selected_logback_pattern(
                xml_path,
                log_dir,
                selected_files=selected_files,
            )
        except ValueError as exc:
            message = str(exc)
            status.update(message)
            self._analysis_log_pattern = None
            self._set_pattern_preview(
                f"XML（{Path(xml_path).name}）",
                f"無可用 Pattern：{message}",
                seed_pattern=None,
            )
            raise

        self._analysis_log_pattern = best_pattern.pattern
        self._set_pattern_preview(
            f"XML（{Path(xml_path).name}）",
            best_pattern.pattern,
            seed_pattern=best_pattern.pattern,
        )
        status.update(
            f"已載入 {best_pattern.name}，命中 {best_pattern.matches}/{best_pattern.checked}"
        )
        return best_pattern.pattern

    def _autofill_logback_settings(
        self,
        log_dir: str,
        selected_files: tuple[str, ...] | None = None,
    ) -> None:
        if self.query_one("#manual_pattern_override", Checkbox).value:
            return

        try:
            source = (
                self._resolve_log_source(log_dir)
                if selected_files is None
                else select_log_source(log_dir, selected_files=selected_files)
            )
        except (OSError, ValueError):
            return

        normalized_source = self._normalize_recipe_source(source.path)
        if normalized_source == getattr(self, "_recipe_autofill_source", ""):
            return

        xml_input = self.query_one("#logback_xml_path", Input)
        current_xml = xml_input.value.strip()
        previous_auto_xml = self._auto_logback_xml_path
        can_update_xml = not current_xml or current_xml == previous_auto_xml
        if not os.path.isdir(source.root):
            if current_xml and current_xml == previous_auto_xml:
                xml_input.value = ""
                self._auto_logback_xml_path = ""
                self._auto_logback_xml_paths = ()
                self._last_logback_autofill_source = ""
                self._sync_pattern_preview_for_current_selection()
            return

        if (
            normalized_source == self._last_logback_autofill_source
            and current_xml == previous_auto_xml
        ) or not can_update_xml:
            return

        xml_paths = self._find_logback_xml_candidates(source.root)
        if not xml_paths:
            if current_xml and current_xml == previous_auto_xml:
                xml_input.value = ""
                self._auto_logback_xml_path = ""
            self._auto_logback_xml_paths = ()
            self._last_logback_autofill_source = ""
            self.query_one("#logback-xml-status", Static).update("")
            self._sync_pattern_preview_for_current_selection()
            return

        best_xml_path, best_pattern = self._find_best_logback_xml_candidate(source)
        best_xml_path = best_xml_path or xml_paths[0]

        xml_input.value = best_xml_path
        self._auto_logback_xml_path = best_xml_path
        self._auto_logback_xml_paths = tuple(xml_paths)
        status = self.query_one("#logback-xml-status", Static)
        if best_pattern is not None:
            candidate_xml_path = getattr(best_pattern, "xml_path", "") or best_xml_path
            self._set_pattern_preview(
                f"XML（{Path(candidate_xml_path).name}）",
                best_pattern.pattern,
                seed_pattern=best_pattern.pattern,
            )
            status.update(
                f"已自動載入 {best_pattern.name[:80]}（{Path(candidate_xml_path).name[:120]}），"
                f"命中 {best_pattern.matches}/{best_pattern.checked}"
            )
        else:
            assessment = assess_logback_patterns(
                xml_paths,
                source.root,
                selected_files=source.filenames,
            )
            reason = assessment.reason
            self._set_pattern_preview(
                f"XML（{len(xml_paths)} 份）"
                if len(xml_paths) > 1
                else f"XML（{Path(best_xml_path).name}）",
                reason,
                seed_pattern=None,
            )
            status.update(reason)
        self._last_logback_autofill_source = normalized_source

    def _find_best_logback_xml_candidate(
        self,
        source: LogSourceSelection,
    ) -> tuple[str, LogbackPatternCandidate | None]:
        xml_paths = self._find_logback_xml_candidates(source.root)
        if not xml_paths:
            return "", None
        if len(xml_paths) == 1:
            xml_path = xml_paths[0]
            try:
                candidate = find_best_logback_pattern(
                    xml_path,
                    source.root,
                    selected_files=source.filenames,
                )
            except (OSError, ParseError, ValueError):
                candidate = None
            candidate_xml_path = getattr(candidate, "xml_path", "") if candidate else ""
            return candidate_xml_path or xml_path, candidate

        assessment = assess_logback_patterns(
            xml_paths,
            source.root,
            selected_files=source.filenames,
        )
        if assessment.status == "ready" and assessment.selected is not None:
            return assessment.selected.xml_path, assessment.selected
        return xml_paths[0], None

    def _find_logback_xml_candidates(self, log_dir: str) -> list[str]:
        candidates: list[str] = []
        try:
            filenames = sorted(os.listdir(log_dir))
        except OSError:
            return candidates

        for filename in filenames:
            lower = filename.lower()
            if not lower.startswith("logback") or not lower.endswith(".xml"):
                continue

            candidate_path = os.path.join(log_dir, filename)
            if os.path.isfile(candidate_path):
                candidates.append(candidate_path)

        return candidates

    def _sync_pattern_field_visibility(self) -> None:
        pattern_field = self.query_one("#pattern-field", Container)
        manual_override = self.query_one("#manual_pattern_override", Checkbox).value
        pattern_field.display = manual_override
        if getattr(self, "is_mounted", False):
            self.query_one("#pattern-source", Static).display = not manual_override
            details = self.query_one("#pattern-details", Collapsible)
            details.display = not manual_override
            if not manual_override:
                details.collapsed = True

    def _set_pattern_preview(
        self, source: str, pattern: str, seed_pattern: str | None = None
    ) -> None:
        self._preview_seed_pattern = seed_pattern
        self.query_one("#pattern-source", Static).update(f"格式來源：{source}")
        self.query_one("#pattern-preview", Static).update(f"Pattern 資訊：{pattern}")
        self.call_after_refresh(self._refresh_condition_overview)

    def _sync_pattern_preview_for_current_selection(self) -> None:
        if self.query_one("#manual_pattern_override", Checkbox).value:
            pattern = self.query_one("#log_pattern", Input).value.strip()
            self._set_pattern_preview(
                "手動覆寫", pattern or "請輸入 Pattern", seed_pattern=pattern or None
            )
            return

        xml_path = self.query_one("#logback_xml_path", Input).value.strip()
        if xml_path:
            self._set_pattern_preview(
                f"XML（{Path(xml_path).name}）",
                "執行分析時解析 XML",
                seed_pattern=None,
            )
            return

        self._set_pattern_preview(
            "預設 Logback",
            DEFAULT_LOGBACK_PATTERN,
            seed_pattern=DEFAULT_LOGBACK_PATTERN,
        )
