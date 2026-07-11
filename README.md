# Java Logback 分析工具

`Java Log Analyzer` 是一個用來解析 Java Logback 日誌的工具，支援 TUI 互動操作與 CLI 指令列分析，可套用關鍵字、時間區間與自訂 Pattern 進行篩選，並匯出成多種格式

## 核心特色

- **智慧解析**：支援常見 Logback 格式，並可處理多行 Exception 堆疊
- **自訂 Pattern**：可直接貼上 `logback.xml` 的 `<pattern>` 內容，分析非預設格式的日誌
- **TUI 介面**：提供全螢幕互動式操作，適合手動分析與快速排查
- **多格式匯出**：支援 `CSV`、`JSON`、`Markdown`
- **精準篩選**：支援 Log Level、關鍵字、忽略大小寫、時間區間與排序方式設定
- **分析摘要**：可快速查看例外群組、時間熱點，以及 Logger / Thread 分布
- **自動分割**：匯出檔案過大時，會自動拆分成多個檔案，也可依 Log Level 分檔輸出

## 安裝方式

### 1. 取得專案

可從 Release 下載壓縮檔後解壓縮，或直接使用原始碼專案

### 2. 建立虛擬環境

```bash
python3 -m venv venv
source venv/bin/activate
```

Windows 可使用：

```powershell
python -m venv venv
.\venv\Scripts\activate
```

### 3. 安裝依賴

```bash
pip install --upgrade pip setuptools
pip install -r requirements.txt
pip install -e .
```

安裝完成後可直接使用 `log-analyzer` 指令

## 啟動方式

### TUI 介面

直接執行：

```bash
log-analyzer
```

也可使用：

```bash
python -m log_analyzer
```

### CLI 介面

若要明確走命令列參數，也可以直接帶入目錄與條件：

```bash
log-analyzer /path/to/logs
```

## TUI 使用重點

- `Log 目錄` 與 `目標資料夾` 可透過「瀏覽」選擇
- `Logback XML` 可載入 `logback.xml` 或 `logback-spring.xml`，並自動挑選最符合樣本的 Pattern
- `Log` 格式可使用預設模式，也可切換到進階 Pattern 手動指定
- `時間區間` 採日期與時間分開輸入
- `Log 層級` 預設分析 `ERROR / WARN / INFO`，可手動勾選 `DEBUG / TRACE` 或使用全選
- `依 Level 分檔` 會依實際有資料的 Log Level 分開匯出；只有一個 Level 有資料時仍維持單檔
- `關鍵字` 旁提供清除按鈕
- 輸入框支援 `Ctrl+A` 全選、`Ctrl+U` 清空
- 介面會記住上次使用的主要條件
- `顯示模式` 提供 `預設摘要` 與 `濃縮摘要` 兩種結果呈現方式
- 分析完成後，結果區會顯示摘要與統計資訊，方便快速判讀

## CLI 使用範例

### 基本分析

```bash
log-analyzer /path/to/logs
```

### 搜尋關鍵字並忽略大小寫

```bash
log-analyzer -k "SQLException" -i
```

### 指定輸出格式

```bash
log-analyzer -f md -o report.md
```

### 依 Level 排序

```bash
log-analyzer /path/to/logs --sort level
```

### 指定分析 Level

預設只分析 `ERROR / WARN / INFO`。若要指定層級，可重複使用 `--level`：

```bash
log-analyzer /path/to/logs --level ERROR --level WARN
```

若要分析所有常見層級：

```bash
log-analyzer /path/to/logs --level ALL
```

`ALL` 代表 `ERROR / WARN / INFO / DEBUG / TRACE`；若和其他 `--level` 混用，會以 `ALL` 為準。

### 指定時間區間

```bash
log-analyzer --start "2026-06-06 10:00:00" --end "2026-06-06 12:00:00"
```

`--start` / `--end` 支援以下格式：

- `YYYY-MM-DD`
- `YYYY-MM-DD HH:MM`
- `YYYY-MM-DD HH:MM:SS`
- `YYYYMMDD`
- `YYYYMMDDHHMM`
- `YYYYMMDDHHMMSS`

### 自訂 Pattern

```bash
log-analyzer /path/to/logs --pattern "%d{yyyy-MM-dd HH:mm:ss.SSS} %-5level [%thread] %logger{36} - %msg%n"
```

### 從 Logback XML 匯入 Pattern

```bash
log-analyzer /path/to/logs --logback-xml /path/to/logback-spring.xml
```

### 調整輸出檔大小門檻

```bash
log-analyzer /path/to/logs --max-export-mb 50
```

### 依 Log Level 分檔輸出

```bash
log-analyzer /path/to/logs --split-by-level
```

啟用後會先判斷各 Level 是否有資料；若只有一個 Level 有資料，仍輸出單一檔案，避免產生不必要的分檔。

### 查看版本

```bash
log-analyzer --version
```

## Pattern 支援範圍

預設模式使用內建 Pattern：

```text
%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n
```

進階模式可貼上 `logback.xml` 的 `<pattern>` 內容，但目前只支援常見 token超出支援範圍時，工具會提示不支援的項目

- 會解析成欄位：`%d` / `%date`、`%thread` / `%t`、`%level` / `%le` / `%p`、`%logger` / `%lo` / `%c`、`%msg` / `%message` / `%m`
- 可接受但不納入輸出欄位：`%file`、`%line` / `%L`、`%class`、`%method` / `%M`、`%caller`、`%mdc` / `%X`、`%ex` / `%throwable` / `%xEx` / `%wEx` / `%wex` / `%rootException` / `%rEx`、`%n`
- 日期格式目前支援 `yyyy-MM-dd HH:mm:ss.SSS`、`yyyy-MM-dd HH:mm:ss,SSS`，以及無毫秒的 `yyyy-MM-dd HH:mm:ss`
- 不支援複雜轉換函式與條件式 Pattern，例如 `%replace(...)`、`%clr(...)`

## 匯出行為

- 預設會先輸出單一報表
- 可用 TUI 的 `依 Level 分檔` 或 CLI 的 `--split-by-level` 依 Log Level 分檔
- Level 分檔只會對實際有資料的 Level 建檔；只有一個 Level 有資料時維持單檔
- 當匯出內容超過大小門檻時，工具會自動拆分成多個檔案
- 若同時啟用 Level 分檔與大小分割，檔名會包含 Level，例如 `report_ERROR_part001.csv`
- 目前預設分割門檻為 `50MB`
- CLI 可透過 `--max-export-mb` 調整門檻
- 若發生分割，摘要檔會列出實際產生的檔案，方便追蹤

## 專案結構

- `src/log_analyzer/application/analysis_service.py`：分析流程與結果組裝
- `src/log_analyzer/domain/parser.py`：核心解析邏輯
- `src/log_analyzer/infrastructure/exporter.py`：多格式匯出與自動分割
- `src/log_analyzer/presentation/cli.py`：CLI 進入點
- `src/log_analyzer/presentation/tui.py`：TUI 介面
- `src/log_analyzer/presentation/tui_views.py`：TUI 結果畫面組件
- `src/log_analyzer/presentation/error_messages.py`：錯誤提示整理
- `src/log_analyzer/presentation/recent_form_state.py`：最近一次表單狀態保存
- `src/log_analyzer/tcss/`：TUI 樣式檔

## 適用環境

- Python 3.9+
- Windows / Linux / macOS

## 授權

MIT License. 請參閱 [LICENSE](LICENSE)
