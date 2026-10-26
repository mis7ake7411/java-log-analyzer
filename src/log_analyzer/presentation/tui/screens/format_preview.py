"""分析前的唯讀格式預覽；抽樣工作在背景執行，不改寫主表單。"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from log_analyzer.domain import logback_xml
from log_analyzer.domain.log_sources import LogSourceSelection
from log_analyzer.domain.logback_pattern import DEFAULT_LOGBACK_PATTERN


class FormatPreviewScreen(ModalScreen[None]):
    """以開啟畫面當下的來源與格式設定顯示少量樣本。"""

    BINDINGS = [
        Binding("escape", "close", "關閉"),
        Binding("enter,c,a,ctrl+x", "ignore", show=False),
    ]
    DEFAULT_CSS = """
    FormatPreviewScreen { align: center middle; }
    #format-preview-card {
        width: 92%; max-width: 110; min-width: 0;
        height: 90%; min-height: 10;
        border: round #38bdf8; background: #0f172a; padding: 0 1;
    }
    #format-preview-title { height: 1; text-style: bold; color: #38bdf8; }
    #format-preview-status { height: auto; margin-bottom: 1; }
    #format-preview-body { height: 1fr; min-height: 0; overflow-x: hidden; }
    #format-preview-content { height: auto; width: 1fr; min-width: 0; text-wrap: wrap; }
    #close-format-preview { height: 3; width: 100%; margin-top: 1; }
    """

    def __init__(
        self,
        source: LogSourceSelection,
        *,
        manual_pattern: str | None = None,
        xml_paths: tuple[str, ...] = (),
        manual_override: bool = False,
    ) -> None:
        """固定預覽來源，手動設定的優先序高於 XML。

        Args:
            source: 已固定檔案範圍的日誌來源。
            manual_pattern: 手動輸入的 Pattern。
            xml_paths: 指定或自動找到的候選 XML 路徑。
            manual_override: 是否使用手動設定；空 Pattern 會顯示錯誤。
        """
        super().__init__()
        self.source = source
        self.manual_pattern = manual_pattern
        self.xml_paths = xml_paths
        self.manual_override = manual_override
        self.preview_loading = True
        self.preview_result: logback_xml.LogbackPatternAssessment | None = None
        self.preview_error: str | None = None

    def compose(self) -> ComposeResult:
        """建立可捲動的樣本區與固定的關閉按鈕。

        Returns:
            Textual 要掛載的預覽元件。
        """
        with Container(id="format-preview-card"):
            yield Static("預覽解析格式", id="format-preview-title", markup=False)
            with VerticalScroll(id="format-preview-body"):
                yield Static("正在抽樣日誌…", id="format-preview-status", markup=False)
                yield Static("", id="format-preview-content", markup=False)
            yield Button("關閉預覽", id="close-format-preview")

    def on_mount(self) -> None:
        self.run_worker(self._load_preview(), name="format-preview", exclusive=True)

    def _read_preview(self) -> logback_xml.LogbackPatternAssessment:
        """在背景執行緒讀取有限樣本，沿用正式 XML 候選評估。

        Returns:
            候選 Pattern、樣本欄位與未命中原因。

        Raises:
            OSError: 來源無法讀取。
            ValueError: 手動 Pattern 或來源設定無效。
        """
        if self.xml_paths and not self.manual_override:
            return logback_xml.assess_logback_patterns(
                self.xml_paths, self.source.root, selected_files=self.source.filenames
            )
        pattern = (self.manual_pattern or "") if self.manual_override else None
        candidate = logback_xml.preview_logback_pattern(
            pattern, self.source.root, selected_files=self.source.filenames
        )
        return logback_xml.LogbackPatternAssessment(
            "unsupported" if candidate.error else "preview",
            "手動覆寫 Pattern" if self.manual_override else "內建 Logback 格式",
            (candidate,),
            None,
            candidate.checked,
        )

    async def _load_preview(self) -> None:
        """非同步呈現抽樣結果，離開畫面後不再更新元件。

        Returns:
            None；已知讀取錯誤以原始原因顯示於預覽畫面。
        """
        try:
            result = await asyncio.to_thread(self._read_preview)
            if not self.is_mounted:
                return
            self.preview_result = result
            self.query_one("#format-preview-status", Static).update(
                f"{result.reason}\n抽樣 {result.checked} 個非空行；僅拆解欄位，不套用篩選條件。"
            )
            self.query_one("#format-preview-content", Static).update(
                self._format_result(result)
            )
        except (OSError, ValueError) as exc:
            if self.is_mounted:
                self.preview_error = str(exc)
                self.query_one("#format-preview-status", Static).update(
                    f"無法預覽：{exc}"
                )
        finally:
            self.preview_loading = False

    def _format_result(self, result: logback_xml.LogbackPatternAssessment) -> str:
        """將預覽資料轉為純文字，避免日誌內容被當成標記。

        Args:
            result: 完成抽樣的候選評估。

        Returns:
            分區呈現來源、Pattern、命中與未命中樣本的文字。
        """
        lines = [
            f"來源：{self.source.path}",
            f"本次選取 {len(self.source.filenames)} 個檔案",
            "",
        ]
        labels = {
            "timestamp": "時間",
            "level": "Level",
            "thread": "Thread",
            "logger": "Logger",
            "message": "訊息",
        }
        for index, candidate in enumerate(result.candidates, 1):
            lines.extend([f"候選 {index}：{candidate.name}"])
            if candidate.xml_path:
                lines.append(f"XML：{candidate.xml_path}")
            lines.append(f"Pattern：{candidate.pattern or DEFAULT_LOGBACK_PATTERN}")
            if candidate.error:
                lines.extend([f"不支援：{candidate.error}", ""])
                continue
            lines.append(f"格式命中 {candidate.matches} / {candidate.checked} 個抽樣行")
            sampled_files = sum(score.checked > 0 for score in candidate.file_scores)
            lines.append(
                f"已取樣 {sampled_files} / {len(self.source.filenames)} 個檔案"
            )
            if candidate.checked == 0:
                lines.append("沒有可用的非空樣本。")
            for sample in candidate.previews:
                lines.extend(
                    [
                        "",
                        "命中樣本" if sample.fields is not None else "未命中樣本",
                        f"原文：{sample.raw}",
                    ]
                )
                if sample.fields is None:
                    lines.append(
                        "原因：此行不符合 Pattern；可能是堆疊續行或不同的日誌格式。"
                    )
                else:
                    for field, label in labels.items():
                        lines.append(
                            f"  {label}：{sample.fields.get(field) or '（未提供）'}"
                        )
            lines.append("")
        lines.append(
            "最多抽樣 50 個非空行，每個候選顯示最多 3 個命中及 3 個未命中樣本（各欄位最多 500 字元）。欄位拆解不等同完整分析驗證。"
        )
        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-format-preview":
            event.stop()
            self.action_close()

    def action_close(self) -> None:
        """關閉預覽；背景工作會隨畫面卸載而取消等待。"""
        self.dismiss(None)

    def action_ignore(self) -> None:
        """攔截主畫面的操作快捷鍵，讓預覽維持唯讀。"""
