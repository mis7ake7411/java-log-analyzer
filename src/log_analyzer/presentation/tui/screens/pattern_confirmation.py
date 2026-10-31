from __future__ import annotations
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from textual.containers import VerticalScroll
from log_analyzer.domain.logback.xml import (
    LogbackPatternAssessment,
    LogbackPatternCandidate,
)


class LogbackPatternConfirmationScreen(ModalScreen[LogbackPatternCandidate | None]):
    """展示不確定的 Logback Pattern 與樣本，等待使用者明確選擇。"""

    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, assessment: LogbackPatternAssessment) -> None:
        self.assessment = assessment
        super().__init__()

    def compose(self):
        message = (
            f"{self.assessment.reason} 抽樣 {self.assessment.checked} 個非空行；"
            "多檔分配最多 50 行，堆疊續行可能影響比例。請選擇可用的 Pattern。"
        )
        with (
            Container(id="pattern-confirmation"),
            Container(id="pattern-confirmation-card"),
        ):
            yield Static("確認 Logback Pattern", id="pattern-confirmation-title")
            yield Static(message, id="pattern-confirmation-message", markup=False)
            with VerticalScroll(id="pattern-candidates"):
                for index, candidate in enumerate(self.assessment.candidates):
                    with Container(classes="pattern-candidate"):
                        yield Static(
                            f"{candidate.name[:80]} · {candidate.xml_path[:300]} · 命中 {candidate.matches}/{candidate.checked}",
                            classes="pattern-candidate-title",
                            markup=False,
                        )
                        yield Static(
                            f"Pattern：{candidate.pattern[:500]}",
                            classes="pattern-candidate-pattern",
                            markup=False,
                        )
                        if candidate.file_scores:
                            lines = [
                                f"{item.filename}：命中 {item.matches}/{item.checked}"
                                if item.checked
                                else f"{item.filename}：未取到樣本（空檔或額度不足）"
                                for item in candidate.file_scores[:20]
                            ]
                            if len(candidate.file_scores) > 20:
                                lines.append(
                                    f"另 {len(candidate.file_scores) - 20} 個檔案未在此列出。"
                                )
                            yield Static("逐檔抽樣\n" + "\n".join(lines), markup=False)
                        if candidate.error:
                            yield Static(
                                f"不可選：{candidate.error[:200]}",
                                classes="pattern-candidate-error",
                                markup=False,
                            )
                        else:
                            yield Button(
                                f"使用此 Pattern（命中 {candidate.matches}/{candidate.checked}）",
                                id=f"pattern-select-{index}",
                                variant="primary",
                            )
                        for preview in candidate.previews:
                            label = (
                                "命中樣本"
                                if preview.fields is not None
                                else "未命中樣本"
                            )
                            yield Static(
                                f"{label}：{preview.raw}",
                                classes="pattern-candidate-preview",
                                markup=False,
                            )
                            if preview.fields is not None:
                                parsed = " | ".join(
                                    f"{name}={value}"
                                    for name, value in preview.fields.items()
                                )
                                yield Static(
                                    f"解析：{parsed}",
                                    classes="pattern-candidate-preview",
                                    markup=False,
                                )
            with Container(id="pattern-confirmation-actions"):
                yield Button("取消分析", id="pattern-cancel")

    def on_mount(self) -> None:
        self.query_one("#pattern-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "pattern-cancel":
            self.dismiss(None)
            return
        if not button_id.startswith("pattern-select-"):
            return
        try:
            index = int(button_id.removeprefix("pattern-select-"))
            candidate = self.assessment.candidates[index]
        except (ValueError, IndexError):
            return
        if candidate.error is None:
            self.dismiss(candidate)
