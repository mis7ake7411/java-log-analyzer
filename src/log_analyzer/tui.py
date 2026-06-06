from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Input, Button, Label, Checkbox, Select, Static
from textual.screen import Screen
from datetime import datetime
import os
from .parser import parse_logs
from .exporter import export_results

class LogAnalyzerApp(App):
    """
    Java Log Analyzer 的 TUI 應用程式。
    使用 Textual 框架打造互動式介面。
    """
    CSS = """
    Container {
        padding: 1 2;
    }
    .field-group {
        margin-bottom: 1;
    }
    Label {
        width: 15;
        text-align: right;
        margin-right: 2;
    }
    Input {
        width: 1fr;
    }
    Button {
        width: 100%;
        margin-top: 1;
    }
    #result-box {
        background: $boost;
        border: solid $accent;
        padding: 1;
        margin-top: 1;
        height: 10;
        display: none;
    }
    .success {
        color: green;
    }
    .error {
        color: red;
    }
    """

    TITLE = "Java Log Analyzer"
    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with Vertical(classes="field-group"):
                with Horizontal():
                    yield Label("Log 目錄:")
                    yield Input(placeholder="例如: ./logs", value=".", id="path")
            
            with Vertical(classes="field-group"):
                with Horizontal():
                    yield Label("搜尋關鍵字:")
                    yield Input(placeholder="例如: Order_123", id="keyword")
            
            with Horizontal(classes="field-group"):
                yield Label("忽略大小寫:")
                yield Checkbox(value=True, id="ignore_case")
            
            with Horizontal(classes="field-group"):
                yield Label("輸出格式:")
                yield Select([("CSV (Excel)", "csv"), ("JSON (程式)", "json"), ("Markdown (報告)", "md")], value="csv", id="format")
            
            yield Button("開始分析", variant="primary", id="run")
            
            yield Static(id="result-box")
            
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            await self.run_analysis()

    async def run_analysis(self) -> None:
        # 取得畫面上的數值
        path = self.query_one("#path", Input).value
        keyword = self.query_one("#keyword", Input).value
        ignore_case = self.query_one("#ignore_case", Checkbox).value
        fmt = self.query_one("#format", Select).value
        
        result_box = self.query_one("#result-box", Static)
        result_box.styles.display = "block"
        
        try:
            # 1. 執行解析
            counts, errors = parse_logs(path, keyword=keyword, ignore_case=ignore_case)
            
            if not counts and not errors:
                result_box.update("[bold red]結果：找不到符合條件的日誌內容。[/]")
                return

            # 2. 執行匯出
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = f"log_analysis_{timestamp}.{fmt}"
            export_results(counts, errors, output_path, fmt)
            
            # 3. 顯示成功訊息
            summary = "\n".join([f"- {lvl}: {cnt}" for lvl, cnt in sorted(counts.items()) if cnt > 0])
            result_box.update(
                f"[bold green]分析完成！[/]\n"
                f"報表儲存至: [cyan]{output_path}[/]\n\n"
                f"[bold]符合條件的統計:[/]\n{summary}"
            )
            
        except Exception as e:
            result_box.update(f"[bold red]錯誤：{str(e)}[/]")

if __name__ == "__main__":
    app = LogAnalyzerApp()
    app.run()
