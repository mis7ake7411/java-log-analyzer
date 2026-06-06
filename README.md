# Java Logback 分析工具 (Java Log Analyzer)

這是一個專業的 Python 工具，專為解析 Java Logback 日誌格式而設計，支援多種輸出格式、關鍵字過濾以及現代化的 TUI 互動介面。

## 核心功能
- **智慧解析**：支援標準 Logback 格式，自動捕捉多行 Exception 堆疊 (Stacktrace)。
- **TUI 互動介面**：提供視覺化選單，免記指令即可輕鬆操作。
- **多格式匯出**：支援 CSV (Excel)、JSON (程式讀取) 及 Markdown (文件報告)。
- **精準過濾**：支援關鍵字搜尋（含大小寫切換）與時間區間篩選。
- **懶人模式**：自動偵測 `logs/` 資料夾，自動生成帶時間戳記的檔名。

## 安裝教學

### 1. 下載專案
```bash
git clone git@github.com:mis7ake7411/java-log-analyzer.git
cd java-log-analyzer
```

### 2. 安裝依賴套件
```bash
pip install -r requirements.txt
```

### 3. 以開發模式安裝工具
安裝後即可在系統任何地方直接使用 `log-analyzer` 指令：
```bash
pip install -e .
```

## 使用說明

### 方式 A：啟動 TUI 互動介面 (最推薦)
直接執行不帶參數的指令，即可開啟全螢幕互動介面：
```bash
log-analyzer
```
*(或使用 `python3 -m log_analyzer.cli`)*

### 方式 B：傳統命令列 (CLI)
您可以透過參數進行自動化分析：

*   **基本分析**：
    ```bash
    log-analyzer /path/to/logs
    ```
*   **搜尋關鍵字並忽略大小寫**：
    ```bash
    log-analyzer -k "SQLException" -i
    ```
*   **指定輸出格式 (Markdown)**：
    ```bash
    log-analyzer -f md -o report.md
    ```
*   **時間區間過濾**：
    ```bash
    log-analyzer --start "2026-06-06 10:00:00" --end "2026-06-06 12:00:00"
    ```

## 專案結構
- `src/log_analyzer/parser.py`: 核心解析邏輯（內含詳細中文註解）。
- `src/log_analyzer/exporter.py`: 多格式匯出邏輯 (CSV, JSON, MD)。
- `src/log_analyzer/tui.py`: Textual 互動介面實作。
- `src/log_analyzer/cli.py`: 命令列進入點與參數處理。

## 適用環境
- Python 3.6+
- 跨平台支援：Windows / Linux / macOS
# java-log-analyzer
