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
python3 -m venv .venv
source .venv/bin/activate
```

Windows 可使用：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

### 3. 安裝依賴

```bash
pip install --upgrade pip setuptools
pip install -r requirements.txt
```

安裝完成後可直接使用 `log-analyzer` 指令

## 開發與驗證

安裝完成後，可執行以下檢查：

```bash
ruff check .
python -m pytest -q
```

GitHub Actions 會在 Python 3.10、3.11 與 3.12 上執行相同的 lint 與測試。

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

### 分析後再決定是否匯出

1. 填入 `Log 目錄` 與分析條件，按「開始分析」。這一步只分析並保留結果，不要求有效的輸出位置，也不會產生報表。
2. 在結果區查看摘要，或按「查看事件原文」查看事件座標與來源內容。
3. 要保存報表時，按「匯出結果」，選擇目標資料夾、檔名、格式、明細欄位與是否依 Level 分檔。
4. 按「執行匯出」。也可在輸出資料夾或檔名欄位按 Enter；這會匯出已保留的結果，不會重新掃描來源。

匯出失敗時，修正欄位或輸出位置後即可重試，分析結果仍可瀏覽。修改表單不會改變目前保留的結果；要套用新條件，需重新按「開始分析」。重新分析、清除結果或離開程式會釋放明細暫存，但不會刪除已匯出的報表。

### 輸入與篩選

- `Log 目錄` 與 `目標資料夾` 可透過「瀏覽」選擇
- 終端機若將拖放轉成路徑貼上，可先聚焦 `Log 目錄`、`目標資料夾` 或 `Logback XML` 欄位再拖入；未支援 bracketed paste 的終端機無法使用此方式
- 路徑貼上支援外層引號、`file://` URL 與 URL 編碼；多行內容會被拒絕，驗證失敗時欄位會保留原值
- `Log 目錄` 可貼上資料夾，或 `.log`／`.txt`／`.out` 檔案（會改用其父目錄）；`目標資料夾` 必須可寫；`Logback XML` 僅接受可讀取的 `.xml` 檔案
- `Logback XML` 可載入 `logback.xml` 或 `logback-spring.xml`，並自動挑選最符合樣本的 Pattern
- `Log` 格式可使用預設模式，也可切換到進階 Pattern 手動指定
- `時間區間` 採日期與時間分開輸入
- `Log 層級` 預設分析 `ERROR / WARN / INFO`，可手動勾選 `DEBUG / TRACE`、使用全選，或勾選「不限 Level」解除 Level 篩選
- `依 Level 分檔` 會依實際有資料的 Log Level 分開匯出；只有一個 Level 有資料時仍維持單檔
- `關鍵字` 旁提供清除按鈕
- 輸入框支援 `Ctrl+A` 全選、`Ctrl+U` 清空
- 介面會記住上次使用的主要條件
- `顯示模式` 提供 `預設摘要` 與 `濃縮摘要` 兩種結果呈現方式
- 分析完成後，結果區會顯示摘要與統計資訊，方便快速判讀

沒有符合資料時，結果區會列出掃描檔案數、符合 Pattern 的事件數，以及時間、Level、關鍵字各階段保留的數量。事件數不是實體行數；多行堆疊屬於同一事件。可按「清除時間並重試」、「不限 Level 並重試」或「清除關鍵字並重試」，只解除該項條件後重新分析；未使用的條件會停用對應按鈕。

### 命名分析配方

按「管理分析配方」可保存多份常用條件：

- 輸入名稱後按「儲存目前條件」；使用相同名稱會更新該配方，其他配方不受影響。
- 選擇配方並按「套用」，回填來源、Pattern／XML、時間、關鍵字與大小寫、Level、排序及顯示設定。套用不會自動分析，也不會替換目前結果；確認條件後再按「開始分析」。
- 選擇配方並按「刪除」，只移除選中的配方。

配方不包含輸出位置、報表或日誌本文。配方檔保存在目前使用者的設定目錄，重啟程式後仍可讀回：

- Windows：`%APPDATA%\java-log-analyzer\recipes.json`
- Linux／macOS：`$XDG_CONFIG_HOME/java-log-analyzer/recipes.json`；未設定時使用 `~/.config/java-log-analyzer/recipes.json`
- Windows 未設定 `APPDATA` 時，也使用使用者家目錄下的 `.config/java-log-analyzer/recipes.json`

若配方檔損壞、版本不支援或無法讀取，介面會顯示原因，不會自動清空或覆寫原檔。既有「上次使用條件」的儲存方式不變。

## CLI 使用範例

CLI 維持一次分析並匯出，不需經過 TUI 的匯出確認步驟；既有參數與預設全欄位輸出不變。

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

進階模式可貼上 `logback.xml` 的 `<pattern>` 內容，但目前只支援常見 token；超出支援範圍時，工具會提示不支援的項目。

- 會解析成欄位：`%d` / `%date`、`%thread` / `%t`、`%level` / `%le` / `%p`、`%logger` / `%lo` / `%c`、`%msg` / `%message` / `%m`
- 可接受但不納入輸出欄位：`%file`、`%line` / `%L`、`%class`、`%method` / `%M`、`%caller`、`%mdc` / `%X`、`%ex` / `%throwable` / `%xEx` / `%wEx` / `%wex` / `%rootException` / `%rEx`、`%n`
- 日期格式目前支援 `yyyy-MM-dd HH:mm:ss.SSS`、`yyyy-MM-dd HH:mm:ss,SSS`，以及無毫秒的 `yyyy-MM-dd HH:mm:ss`
- 不支援複雜轉換函式與條件式 Pattern，例如 `%replace(...)`、`%clr(...)`

## 匯出行為

- TUI 在按「執行匯出」後才寫入報表；CLI 仍會分析後直接匯出
- 預設輸出全欄位的單一報表；TUI 可選擇 CSV、JSON、Markdown 的明細欄位，摘要統計仍會保留
- 可用 TUI 的 `依 Level 分檔` 或 CLI 的 `--split-by-level` 依 Log Level 分檔
- Level 分檔只會對實際有資料的 Level 建檔；只有一個 Level 有資料時維持單檔
- 當匯出內容超過大小門檻時，工具會自動拆分成多個檔案
- 若同時啟用 Level 分檔與大小分割，檔名會包含 Level，例如 `report_ERROR_part001.csv`
- 目前預設分割門檻為 `50MB`
- CLI 可透過 `--max-export-mb` 調整門檻
- 若發生分割，摘要檔會列出實際產生的檔案，方便追蹤

### 選用的證據清單

TUI 匯出設定可勾選附加證據清單，預設不勾選。匯出報表後，工具會在同一資料夾產生 `<輸出檔名>_manifest.json`，記錄：

- 工具版本、產生時間，以及該次分析的固定條件與逐階計數
- 來源檔案的相對檔名、掃描時的大小／修改時間與穩定狀態
- 匯出時的來源核對狀態，以及核對通過的來源 SHA256
- 實際產生的報表檔名、大小與 SHA256，包含摘要及分割報表

來源若已變動、遺失、不可讀或無法安全核對，清單會標記原因，不會假裝驗證通過。報表仍使用已保留的分析結果。

這份清單不是日誌快照，也不能證明來源位元組與分析當時完全相同。來源核對依據是大小與奈秒修改時間，SHA256 則在匯出時才計算；即使核對通過，也有這個限制。清單不保存日誌本文或來源的絕對目錄，但分析條件與檔名仍可能含敏感資訊，分享前應先確認內容。

若報表已匯出、只有證據清單失敗，介面會分開顯示報表路徑與失敗原因。修正後可重試，不必重新分析。

## 專案結構

- `src/log_analyzer/application/analysis_service.py`：分析流程與結果組裝
- `src/log_analyzer/application/evidence_manifest.py`：證據清單與來源／報表雜湊核對
- `src/log_analyzer/domain/parser.py`：核心解析邏輯
- `src/log_analyzer/infrastructure/exporter.py`：多格式匯出與自動分割
- `src/log_analyzer/infrastructure/recipe_store.py`：使用者本地命名配方儲存
- `src/log_analyzer/presentation/cli.py`：CLI 進入點
- `src/log_analyzer/presentation/tui.py`：TUI 介面
- `src/log_analyzer/presentation/tui_views.py`：TUI 結果畫面組件
- `src/log_analyzer/presentation/error_messages.py`：錯誤提示整理
- `src/log_analyzer/presentation/recent_form_state.py`：最近一次表單狀態保存
- `src/log_analyzer/tcss/`：TUI 樣式檔

## 適用環境

- Python 3.10+
- Windows / Linux / macOS

## 授權

MIT License. 請參閱 [LICENSE](LICENSE)
