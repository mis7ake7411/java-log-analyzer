# Java Logback 分析工具 (Java Log Analyzer)

這是一個專業的 Python 工具，專為解析 Java Logback 日誌格式而設計，支援多種輸出格式、關鍵字過濾以及現代化的 TUI 互動介面。

## 核心功能

- **智慧解析**：支援標準 Logback 格式，自動捕捉多行 Exception 堆疊 (Stacktrace)。
- **自訂格式**：支援貼上常見 Logback PatternLayout pattern，解析非預設欄位順序的日誌。
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

### 2. 建立虛擬環境 (建議)

使用虛擬環境可以確保開發環境純淨，避免與系統套件衝突：

- **Windows:**

    ```powershell
    python -m venv venv
    .\venv\Scripts\activate
    ```

- **Linux / macOS:**

    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

- **Ubuntu 18.04 ：**
    如果尚未安裝 `pyenv`，請先依照官方文件( [pyenv](https://github.com/pyenv/pyenv) )完成安裝，再執行下列指令。

    ```bash
    # 安裝 pyenv 後，讓專案使用 Python 3.9，不影響系統的 python3
    pyenv install 3.9.25
    pyenv local 3.9.25
    python -m venv venv
    source venv/bin/activate
    ```

### 3. 安裝依賴套件與工具

在虛擬環境啟動後執行：

```bash
# 升級基礎工具
pip install --upgrade pip setuptools

# 安裝依賴套件
pip install -r requirements.txt

# 以開發模式安裝工具
# 安裝後即可在系統任何地方直接使用 `log-analyzer` 指令
pip install -e .
```

## 使用說明

### 方式 A：啟動 TUI 互動介面 (最推薦)

直接執行不帶參數的指令，即可開啟全螢幕互動介面：

```bash
log-analyzer
```

*(或使用 `python3 -m log_analyzer.cli`)*

TUI 小提示：

- 點選「瀏覽」時，資料夾樹會從系統根目錄開始顯示，方便直接切換到任意磁碟或掛載點。
- 關鍵字欄位旁提供「清除」按鈕，可一鍵清空目前輸入內容。
- 輸入框支援 `Ctrl+A` 全選目前欄位文字，`Ctrl+U` 清空目前欄位文字。
- Log 格式預設使用內建 Logback pattern；若日誌格式不同，可切到「進階 Pattern」並貼上 `logback.xml` 的 `<pattern>` 內容。
- Logback XML 可透過「瀏覽」選擇 `logback.xml` / `logback-spring.xml`，在子視窗按「載入」後，工具會用目前 Log 目錄樣本挑選命中率最高的 pattern 並回填。
- 時間區間採日期與時間分開輸入；起訖欄位都留白時會解析全部 log，不套用時間限制。只填日期時，開始日期會從當天 `00:00:00` 起算，結束日期會到當天 `23:59:59` 為止。

### 方式 B：傳統命令列 (CLI)

您可以透過參數進行自動化分析：

- **基本分析**：

    ```bash
    log-analyzer /path/to/logs
    ```

- **搜尋關鍵字並忽略大小寫**：

    ```bash
    log-analyzer -k "SQLException" -i
    ```

- **指定輸出格式 (Markdown)**：

    ```bash
    log-analyzer -f md -o report.md
    ```

- **依 Level 分組排序**：

    ```bash
    log-analyzer /path/to/logs --sort level
    ```

- **時間區間過濾**：

    ```bash
    log-analyzer --start "2026-06-06 10:00:00" --end "2026-06-06 12:00:00"
    ```

- **自訂 Logback pattern**：

    ```bash
    log-analyzer /path/to/logs --pattern "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} %file:%line - %msg%n"
    ```

- **從 logback.xml 匯入 pattern**：

    ```bash
    log-analyzer /path/to/logs --logback-xml /path/to/logback-spring.xml
    ```

- **Logback 預設逗號毫秒格式**：

    ```bash
    log-analyzer /path/to/logs --pattern "%d %-5level [%thread] %logger{0}: %msg%n"
    ```

- **括號式 level / thread 格式**：

    ```bash
    log-analyzer /path/to/logs --pattern "[%d{yyyy-MM-dd HH:mm:ss.SSS}] [%-5level] [%thread] %logger.%method(%line) - %msg%n"
    ```

- **查看版本**：

    ```bash
    log-analyzer --version
    ```

## Logback Pattern 支援範圍

預設模式使用內建 pattern：

```text
%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n
```

進階模式可貼上 `logback.xml` 裡的 `<pattern>` 內容，但目前只支援常見 token。超出範圍時，CLI/TUI 會提示不支援的 token。Spring Boot 常見的 `${NAME:-default}` 預設值 placeholder 會先展開成 default。

- 會解析成欄位：`%d` / `%date`、`%thread` / `%t`、`%level` / `%le` / `%p`、`%logger` / `%lo` / `%c`、`%msg` / `%message` / `%m`
- 可接受但不納入輸出欄位：`%file`、`%line` / `%L`、`%class`、`%method` / `%M`、`%caller`、`%mdc` / `%X`、`%ex` / `%throwable` / `%xEx` / `%wEx` / `%wex` / `%rootException` / `%rEx`、`%n`
- 日期格式目前支援 `yyyy-MM-dd HH:mm:ss.SSS`、`yyyy-MM-dd HH:mm:ss,SSS`，或無毫秒的 `yyyy-MM-dd HH:mm:ss`
- 尚不支援轉換函式與複雜 pattern，例如 `%replace(...)`、`%clr(...)` 顏色包裝或條件式格式

## 專案結構

- `src/log_analyzer/parser.py`: 核心解析邏輯（內含詳細中文註解）。
- `src/log_analyzer/exporter.py`: 多格式匯出邏輯 (CSV, JSON, MD)。
- `src/log_analyzer/tui.py`: Textual 互動介面實作。
- `src/log_analyzer/cli.py`: 命令列進入點與參數處理。

## 適用環境

- Python 3.9+
- 跨平台支援：Windows / Linux / macOS

## 授權

MIT License. See [LICENSE](LICENSE) for details.
