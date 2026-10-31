from __future__ import annotations
import logging
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button, Checkbox, Collapsible, Label, Select, Static
from log_analyzer.application.analysis.service import DEFAULT_SELECTED_LEVELS
from log_analyzer.domain.logback.pattern import DEFAULT_LOGBACK_PATTERN
from log_analyzer.infrastructure.exporting.fields import (
    EXPORT_FIELDS,
    EXPORT_FIELD_LABELS,
)
from log_analyzer.presentation.tui.components.inputs import ShortcutInput
from log_analyzer.presentation.tui.components.source_scope import SourceScopePreview
from log_analyzer.presentation.tui.components.condition_overview import ConditionsOverview
from log_analyzer.presentation.tui.components.result_notice import ResultContextNotice
from log_analyzer.presentation.tui.components.time_range import TimeRangeSettings
from log_analyzer.version import get_package_version
from log_analyzer.presentation.tui.components.layout import (
    _ResponsiveAdvancedSettings,
    _DashboardResultLog,
)
from log_analyzer.presentation.tui.components.live_panel import LivePanel

logger = logging.getLogger(__name__)


class ViewCompositionMixin:
    def _compose_hero(self) -> ComposeResult:
        with Container(id="hero"), Container(id="hero-title-row"):
            yield Static("Java Log Analyzer", id="hero-title")
            yield Static(f"v{get_package_version()}", id="hero-version")
            yield Static("·", id="hero-separator")
            yield Static("分析、篩選並匯出 Java Logback 記錄", id="hero-subtitle")

    def _compose_basic_input_fields(self) -> ComposeResult:
        yield Static("輸入設定", classes="section-title")

        with Container(classes="field field--inline", id="analysis-mode-field"):
            yield Label("分析模式")
            yield Select(
                [("批次分析", "batch"), ("即時追蹤", "live")],
                value="batch",
                id="analysis-mode",
            )
        yield Static("", classes="field-hint", id="analysis-mode-status", markup=False)

        with Container(classes="field"):
            yield Label("Log 來源（檔案或資料夾）")
            with Container(classes="path-row"):
                yield ShortcutInput(placeholder="例如：./logs", value=".", id="path")
                yield Button("瀏覽", id="browse_path")
        yield Static("", classes="path-status", id="path-status")
        yield ConditionsOverview(id="condition-overview")
        yield SourceScopePreview()

        with Container(classes="field"):
            yield Label("關鍵字")
            with Container(classes="path-row"):
                yield ShortcutInput(
                    placeholder="例如：Order_123 或 SQLException", id="keyword"
                )
                yield Button("清除", id="clear_keyword")
        yield Static("", classes="field-hint")

        yield Button(
            "展開進階設定", id="toggle_advanced_settings", classes="advanced-toggle"
        )
        yield Static("", classes="field-hint")

    def _compose_advanced_settings(self) -> ComposeResult:
        with _ResponsiveAdvancedSettings(
            id="advanced-settings", classes="advanced-settings"
        ):
            with Container(id="advanced-settings-title-row"):
                yield Static("進階設定", classes="section-title section-title--sub")

            yield from self._compose_advanced_output_settings()
            yield from self._compose_time_settings()
            yield from self._compose_display_settings()
            yield from self._compose_level_settings()

    def _compose_advanced_output_settings(self) -> ComposeResult:
        with Container(classes="field"):
            yield Label("Logback XML")
            with Container(classes="path-row compact-buttons"):
                yield ShortcutInput(
                    placeholder="例如：./logback-spring.xml", id="logback_xml_path"
                )
                yield Button("瀏覽", id="browse_logback_xml")
        yield Static("", classes="field-hint", id="logback-xml-status")

        with Container(classes="field"):
            yield Checkbox(
                "手動覆寫 Pattern", value=False, id="manual_pattern_override"
            )

        with Container(classes="field", id="pattern-field"):
            yield Label("Pattern")
            with Container(classes="field-body"):
                yield ShortcutInput(
                    placeholder=DEFAULT_LOGBACK_PATTERN, id="log_pattern"
                )
                yield Static("僅支援常見 Logback token", classes="field-hint")
        yield Static(
            "格式來源：預設 Logback",
            classes="field-hint",
            id="pattern-source",
            markup=False,
        )
        with Collapsible(
            title="檢視套用的 Pattern", collapsed=True, id="pattern-details"
        ):
            yield Static(
                f"有效 Pattern：{DEFAULT_LOGBACK_PATTERN}",
                classes="field-hint",
                id="pattern-preview",
                markup=False,
            )

    def _compose_time_settings(self) -> ComposeResult:
        yield TimeRangeSettings()

    def _compose_display_settings(self) -> ComposeResult:
        with (
            Container(classes="field-row"),
            Container(classes="field field--inline field--ignore"),
        ):
            yield Label("忽略大小寫")
            yield Checkbox(value=True, id="ignore_case")

        with (
            Container(classes="field-row"),
            Container(classes="field field--inline field--sort"),
        ):
            yield Label("排序方式")
            yield Select(
                [("時間排序", "time"), ("Level 分組", "level")],
                value="time",
                id="sort_by",
            )

        yield Static("", classes="field-hint")

    def _compose_export_settings(self) -> ComposeResult:
        with Container(id="export-settings", classes="export-settings"):
            yield Static("匯出設定", classes="section-title")
            yield Static("", id="export-result-context", markup=False)
            with Container(classes="field"):
                yield Label("目標資料夾")
                with Container(classes="path-row"):
                    yield ShortcutInput(
                        placeholder="例如：./exports", value=".", id="output_path"
                    )
                    yield Button("瀏覽", id="browse_output_path")
                yield Static("", classes="path-status", id="output-path-status")

            with Container(classes="field"):
                yield Label("輸出檔名")
                with Container(classes="field-body"):
                    yield ShortcutInput(
                        placeholder="例如：analysis_123000",
                        value=self._auto_output_name,
                        id="output_name",
                    )
                    yield Static("副檔名由輸出格式自動決定", classes="field-hint")

            with Container(classes="field-row"):
                with Container(classes="field field--inline field--format"):
                    yield Label("輸出格式")
                    yield Select(
                        [("CSV (Excel)", "csv"), ("JSON", "json"), ("Markdown", "md")],
                        value="csv",
                        id="format",
                    )
                with Container(classes="field field--inline field--split-output"):
                    yield Label("依 Level 分檔")
                    yield Checkbox(value=False, id="split_by_level")

            yield Label("輸出欄位", classes="field-label")
            with Container(id="export-fields", classes="export-fields"):
                for field in EXPORT_FIELDS:
                    yield Checkbox(
                        EXPORT_FIELD_LABELS[field],
                        value=True,
                        id=f"export_field_{field}",
                    )

            yield Static("", id="export-status", classes="export-status", markup=False)
            yield Checkbox("包含證據清單（JSON）", value=False, id="include_manifest")
            yield Static(
                "證據清單包含分析條件、來源核對與報表雜湊，不含日誌內容快照。來源核對比對掃描時記錄的檔案大小與修改時間，以及匯出時計算雜湊前後的檔案大小與修改時間；不證明掃描時的日誌內容不可變。",
                classes="field-hint",
                id="manifest-hint",
                markup=False,
            )
            yield Button("執行匯出", variant="primary", id="confirm-export")

    def _compose_level_settings(self) -> ComposeResult:
        with Container(classes="field"):
            yield Label("Log 層級")
            with Container(classes="field-body"):
                with Container(classes="level-filter-row"):
                    for level in ("ERROR", "WARN", "INFO", "DEBUG", "TRACE"):
                        yield Checkbox(
                            level,
                            value=level in DEFAULT_SELECTED_LEVELS,
                            id=f"level_{level}",
                        )
                yield Checkbox("不限 Level", value=False, id="level_unrestricted")
                with Container(classes="level-filter-actions"):
                    yield Button("全選", id="select_all_levels")
                    yield Button("清空", id="clear_levels")
                yield Static(
                    "不限時包含所有層級（含其他）；關閉後使用已勾選層級。",
                    classes="field-hint",
                )

    def _compose_actions(self) -> ComposeResult:
        with Container(id="form-actions"):
            yield Button("管理分析配方", id="manage-recipes")
            yield Button("開始分析", variant="primary", id="run")
            yield Button("取消分析", id="cancel-analysis", disabled=True)
        yield Static("", id="recipe-feedback", classes="recipe-feedback", markup=False)

    def _compose_result_panel(self) -> ComposeResult:
        with Container(id="output-panel"):
            yield Static("執行結果", classes="section-title", id="output-title")
            yield Static(
                "",
                id="live-summary-metadata",
                classes="live-summary-metadata",
                markup=False,
            )
            yield Button(
                "查看完整條件與缺口",
                id="live-summary-details",
                classes="live-summary-details",
            )
            yield Static("", id="result-status")
            yield ResultContextNotice("", id="result-context-notice", markup=False)
            yield _DashboardResultLog(
                id="result-box",
                markup=True,
                highlight=False,
                auto_scroll=False,
                wrap=True,
                min_width=0,
            )
            with Container(id="zero-result-actions", classes="zero-results-hidden"):
                yield Button("清除時間並重試", id="retry_without_time", disabled=True)
                yield Button(
                    "不限 Level 並重試", id="retry_without_level", disabled=True
                )
                yield Button(
                    "清除關鍵字並重試", id="retry_without_keyword", disabled=True
                )
            with Container(id="output-actions"):
                yield Button("查看事件原文", id="browse-events", disabled=True)
                yield Button("匯出結果", id="export-result", disabled=True)
                yield Button("清除結果", id="clear")
                yield Button("返回即時追蹤", id="return-live")
            yield LivePanel(id="live-panel")
