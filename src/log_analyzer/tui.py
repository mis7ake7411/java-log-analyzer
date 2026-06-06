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
    .path-preview {
        color: $text-muted;
        margin-left: 17;
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
    """

    TITLE = "Java Log Analyzer"
    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with Vertical(classes="field-group"):
                with Horizontal():
                    yield Label("Log 目錄:")
                    yield Input(placeholder="相對路徑 (如 ./logs) 或絕對路徑", value=".", id="path")
                yield Static("", id="abspath-preview", classes="path-preview")
            
            with Vertical(classes="field-group"):
                with Horizontal():
                    yield Label("輸出檔名:")
                    # 預設帶出一個帶時間的名稱
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    yield Input(placeholder="例如: my_report.csv", value=f"analysis_{timestamp}.csv", id="output_name")

            with Vertical(classes="field-group"):
                with Horizontal():
                    yield Label("搜尋關鍵字:")
                    yield Input(placeholder="例如: Order_123 (留空則分析全部)", id="keyword")
            
            with Horizontal(classes="field-group"):
                yield Label("忽略大小寫:")
                yield Checkbox(value=True, id="ignore_case")
            
            with Horizontal(classes="field-group"):
                yield Label("輸出格式:")
                yield Select([("CSV (Excel)", "csv"), ("JSON (程式)", "json"), ("Markdown (報告)", "md")], value="csv", id="format")
            
            yield Button("開始分析", variant="primary", id="run")
            
            yield Static(id="result-box")
            
        yield Footer()

    def on_mount(self) -> None:
        """應用程式啟動時，先更新一次路徑預覽"""
        self.update_path_preview(".")

    def on_input_changed(self, event: Input.Changed) -> None:
        """當使用者在輸入框打字時即時觸發"""
        if event.input.id == "path":
            self.update_path_preview(event.value)

    def update_path_preview(self, path: str) -> None:
        """更新畫面上的絕對路徑預覽，讓使用者知道自己指在哪裡"""
        try:
            if not path:
                abspath = "請輸入路徑..."
            else:
                abspath = os.path.abspath(path)
                if not os.path.exists(abspath):
                    abspath = f"⚠️ 路徑不存在: {abspath}"
                else:
                    abspath = f"✅ 確認路徑: {abspath}"
            self.query_one("#abspath-preview", Static).update(abspath)
        except Exception:
            pass

    async def run_analysis(self) -> None:
        # 取得畫面上的數值
        path = self.query_one("#path", Input).value
        output_name = self.query_one("#output_name", Input).value
        keyword = self.query_one("#keyword", Input).value
        ignore_case = self.query_one("#ignore_case", Checkbox).value
        fmt = self.query_one("#format", Select).value
        
        result_box = self.query_one("#result-box", Static)
        result_box.styles.display = "block"
        
        try:
            # 修正輸出副檔名（如果使用者忘記改的話）
            if not output_name.endswith(f".{fmt}"):
                # 簡單處理，移除舊副檔名加上新的
                base_name = os.path.splitext(output_name)[0]
                output_name = f"{base_name}.{fmt}"

            # 1. 執行解析
            counts, matched_logs = parse_logs(path, keyword=keyword, ignore_case=ignore_case)

            if not matched_logs and not counts:
                result_box.update("[bold red]找不到日誌檔案或無符合條件的內容。[/]")
                return

            # 2. 執行匯出
            export_results(counts, matched_logs, output_name, fmt)

            
            # 3. 顯示成功訊息
            total_logs = sum(counts.values())
            summary = "\n".join([f"- {lvl}: {cnt}" for lvl, cnt in sorted(counts.items()) if cnt > 0])
            result_box.update(
                f"[bold green]分析完成！[/]\n"
                f"報表已儲存至: [cyan]{os.path.abspath(output_name)}[/]\n\n"
                f"[bold]分析結果統計:[/]\n"
                f"- 總計掃描到: {total_logs} 筆日誌\n"
                f"- 合併後產出: {len(matched_logs)} 筆獨特事件\n\n"
                f"[bold]各等級分佈:[/]\n{summary}"
            )
            
        except Exception as e:
            result_box.update(f"[bold red]錯誤：{str(e)}[/]")

if __name__ == "__main__":
    app = LogAnalyzerApp()
    app.run()
