# Java Logback 分析工具

`Java Log Analyzer` 是一個用來解析 Java Logback 日誌的工具，支援 TUI 互動操作與 CLI 指令列分析，可套用關鍵字、時間區間與自訂 Pattern 進行篩選，並匯出成多種格式

## 核心特色

- **智慧解析**：支援常見 Logback 格式，並可處理多行 Exception 堆疊
- **自動套用 Pattern**：可貼上 Logback XML 路徑自動載入格式，也可手動覆寫 Pattern
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

1. 填入 `Log 來源（檔案或資料夾）` 與分析條件，按「開始分析」。開始前會顯示來源模式、檔案數與完整檔名清單；多檔清單可捲動查看。這一步只分析並保留結果，不要求有效的輸出位置，也不會產生報表。
2. 在結果區查看摘要，或按「查看事件原文」查看事件座標與來源內容。
3. 要保存報表時，按「匯出結果」，選擇目標資料夾、檔名、格式、明細欄位與是否依 Level 分檔。
4. 按「執行匯出」。也可在輸出資料夾或檔名欄位按 Enter；這會匯出已保留的結果，不會重新掃描來源。

遇到同名檔時，會列出衝突的完整路徑，提供「改名」、「覆蓋」與「取消」。預設不覆蓋；選擇改名會回到檔名欄位，取消則保留目前分析結果。選擇覆蓋只套用到這一次匯出，不會記成下次的預設。

匯出失敗時，修正欄位或輸出位置後即可重試，分析結果仍可瀏覽。修改表單不會改變目前保留的結果；要套用新條件，需重新按「開始分析」。重新分析成功後才會替換上一份結果並釋放其明細暫存；失敗、取消或沒有符合資料時，仍保留上一份成功結果。清除結果或離開程式也會釋放明細暫存，但不會刪除已匯出的報表。

修改分析條件後，結果區會提示「尚未重新分析」；改回原條件會清除提示。輸出檔名、格式與明細欄位等匯出設定不會觸發此提示。結果摘要會列出該次的時間區間與實際 Pattern；匯出設定另列原條件，避免將目前表單誤認成已保留結果。分析進行期間修改表單，也會在完成後保留這項區別。

### 分析進度與取消

分析期間會顯示目前階段、檔案與已處理行數。可按「取消分析」或 `Ctrl+X` 要求停止；顯示「取消中」時，請等目前工作結束後再重新分析。取消不會強制終止執行緒，單次檔案讀取或排序仍可能需要一些時間。

分析尚未停止時，不可開始第二次分析或匯出。若原本已有成功結果，取消後仍可瀏覽及匯出該份結果；畫面會提示保留的是上次結果，並非本次條件的分析結果。

### 輸入與篩選

- `Log 來源（檔案或資料夾）` 與 `目標資料夾` 可透過「瀏覽」選擇
- 資料夾來源可按範圍預覽中的「選取檔案」，搜尋檔名並以空白鍵勾選；顯示已選數量與總大小。「全選列出／取消列出」只作用於搜尋後列出的檔案，其他勾選會保留。取消不改變目前選取；套用後預覽、XML 抽樣與分析使用相同清單。選取只保留在本次使用期間，切換來源時恢復預設全部。已選檔案若遺失會提示錯誤，不會默默改分析其他檔案。
- 終端機若將拖放轉成路徑貼上，可先聚焦 `Log 來源`、`目標資料夾` 或 `Logback XML` 欄位再拖入；未支援 bracketed paste 的終端機無法使用此方式
- 路徑貼上支援外層引號、`file://` URL 與 URL 編碼；多行內容會被拒絕，驗證失敗時欄位會保留原值
- `Log 來源` 可貼上資料夾或單一 `.log`／`.txt`／`.out` 檔案。副檔名大小寫不拘；資料夾只掃描目前層，不會遞迴子目錄；`目標資料夾` 必須可寫；`Logback XML` 僅接受可讀取的 `.xml` 檔案
- `Logback XML` 可直接貼上 `logback.xml` 或 `logback-spring.xml` 路徑，或按「瀏覽」選取；分析時會載入 XML 並評估 Pattern。指定的 XML 無法讀取或沒有可用 Pattern 時會提示錯誤，不會默默改用預設格式
- 不需選擇格式模式；介面會顯示目前的格式來源，完整 Pattern 可展開詳情查看。未指定 XML 時會嘗試在來源資料夾中尋找 XML，沒有找到才使用內建 Pattern。選擇單一檔案時，其父目錄只用於自動尋找 XML；Pattern 取樣只讀取選中的檔案
- 需要自行調整時，勾選「手動覆寫 Pattern」並輸入內容；手動模式只顯示輸入欄與必要提示，不重複列出整段 Pattern。手動內容優先於 XML；取消勾選後恢復自動套用，保留的手動文字不會影響分析
- 進階設定依表單實際可用寬度調整欄位排列；空間不足時改為直排，仍可捲動到各項設定
- `時間區間` 採日期與時間分開輸入
- `Log 層級` 預設分析 `ERROR / WARN / INFO`，可手動勾選 `DEBUG / TRACE`、使用全選，或勾選「不限 Level」解除 Level 篩選。選擇不限時，個別層級及全選／清除會停用；取消不限後恢復原本勾選
- `依 Level 分檔` 會依實際有資料的 Log Level 分開匯出；只有一個 Level 有資料時仍維持單檔
- `關鍵字` 旁提供清除按鈕
- 輸入框支援 `Ctrl+A` 全選、`Ctrl+U` 清空
- 介面會記住上次使用的主要條件
- 摘要統一依結果區的可用寬度自動排版，不需選擇顯示模式；所有摘要與統計內容皆會保留
- 分析完成後，結果區會顯示摘要與統計資訊；數字卡在寬版並排、窄版直排，Level 長條隨可用寬度伸展並顯示占比
- 時間熱點與 Thread 分布依每次事件出現的時間及執行緒計次；同一事件群組在同一分類內只計一組，跨時段或跨 Thread 時可分別計入各分類，因此各分類的群組數相加不一定等於總群組數。

沒有符合資料時，結果區會列出掃描檔案數、符合 Pattern 的事件數，以及時間、Level、關鍵字各階段保留的數量。事件數不是實體行數；多行堆疊屬於同一事件。可按「清除時間並重試」、「不限 Level 並重試」或「清除關鍵字並重試」，只解除該項條件後重新分析；未使用的條件會停用對應按鈕。

### XML Pattern 可信度與確認

XML 讀取成功不代表格式已匹配。工具對選定來源取樣最多 50 個非空行；多檔時依檔名順序分配額度，為後續檔案保留樣本，空檔或短檔未用完的額度交給後續檔案。這仍是有限抽樣，檔案超過取樣額度時不保證涵蓋每個檔案。評估結果區分：

- **可套用**：有命中、整體及各有樣本檔案的命中率至少 80%，且只有一個最高分的可用 Pattern，直接套用。
- **無法確認**：零命中、低命中、最高分平手或沒有樣本。按「開始分析」後會顯示候選、命中數及少量原文／解析欄位，明確選擇後才繼續；取消或按 Escape 不會分析，也不會清除上一份結果。
- **不支援**：沒有候選或所有候選都無法編譯，會說明原因，不會當成零命中格式繼續套用。仍可用手動 Pattern 覆寫。

自動找到多份 XML 時會一起比較，相同 Pattern 不重複計分；明確指定 XML 時只評估該檔。確認只適用當次分析，不會儲存成配方或永久略過確認。

命中率是「抽樣非空行的格式匹配比例」，不是整份日誌的解析成功率，也不是事件比例。堆疊續行可能拉低比例；預覽文字會截短，原始日誌不會被修改。即使樣本達門檻，仍可能有未取樣內容使用其他格式。

確認視窗會列出各檔抽樣命中數；未取到樣本的檔案會另行標示，不會當成匹配成功。即使整體達到 80%，若個別有樣本的檔案低於門檻，也會要求確認。

### 逐檔解析診斷

TUI 結果與 CLI 完成訊息會列出各檔事件起始行、堆疊行、其他訊息續行、空行及未辨識／疑似不同格式行的數量；沒有符合結果時也可查看。這些數量在時間、Level、關鍵字篩選前計算，不會把合法堆疊當成解析失敗。

未辨識內容保留每檔最多 3 個範例、每個最多 240 字，供本地排查。畫面列出前 20 個檔案並提示省略數；總數包含全部來源。第一個事件前的非空內容，以及未匹配 Pattern 的日期時間起始行，會列為待確認；其餘續行採啟發式歸類，不能據此宣稱整份檔案完全解析。證據清單維持既有格式，不包含這些本文範例。

### 查看事件原文

- 群組頁可搜尋已分析結果中的訊息、Logger 或 Thread，按 Enter 或「套用」後更新；Thread 搜尋包含群組內各次出現的執行緒。
- 可選擇分析原排序、出現次數最多或最近出現。最近出現以逐次紀錄中的最大時間為準，不受來源檔案順序影響。「清除」恢復完整群組與分析原排序。
- 搜尋與排序只影響瀏覽，摘要與匯出仍使用原分析結果。搜尋期間可關閉視窗取消工作；讀取失敗會保留可見頁面並提示原因。
- 事件群組清單分行顯示層級、次數、時間、訊息及來源，保留分析時的排序方式。
- 選擇群組後，出現位置按時間由早到晚排列；時間相同時按檔名、行號排序。
- 返回上一層時保留該層頁碼及選取位置。窄版搜尋與排序分成兩列；矮視窗固定底部操作列，仍可換頁與關閉。
- 詳細頁分開顯示事件資訊、分析時保留的群組代表訊息與例外堆疊，以及已核對來源的原文前後文。群組代表內容不等於每次出現的完整原文；堆疊與原文皆保留原本行序。
- 來源檔案若已變更或無法核對，會顯示警告並停用座標複製，不會將舊的分析內容當成已核對原文。
- 結果分頁使用位置索引，跳到後面的頁面不必從頭讀取所有事件。原文預覽首次定位仍需掃描到目標行，後續在同一瀏覽視窗內查看附近或較早位置時，會重用稀疏索引；每次仍核對來源檔案狀態。

### 日誌編碼

來源日誌以 UTF-8 讀取，支援檔案開頭的 BOM。遇到無效的 UTF-8 位元組時，會以 `�` 呈現並提示受影響的檔案數與行數，不會默默刪除內容。計數包含未符合 Pattern 的行與例外堆疊；原本就合法存在的 `�` 不算解碼錯誤。

XML Pattern 取樣、實際分析與原文預覽使用相同的替代方式。編碼提示不代表來源檔案已變更；通過來源核對的原文仍可查看。工具不會猜測或自動轉換其他編碼，也不會改寫原始日誌；若來源是 Big5 等編碼，請先另存為 UTF-8 再分析。

### 命名分析配方

按「管理分析配方」可保存多份常用條件：

- 輸入名稱後按「儲存目前條件」；使用相同名稱會更新該配方，其他配方不受影響。
- 選擇配方並按「套用」，回填來源、Pattern／XML、時間、關鍵字與大小寫、Level 及排序設定。套用不會自動分析，也不會替換目前結果；確認條件後再按「開始分析」。舊配方的顯示模式值會忽略，統一自動排版。
- 選擇配方並按「刪除」，只移除選中的配方。

新配方會記住自動套用或手動覆寫。舊版進階 Pattern 配方有手動內容時繼續採用；若只有 XML 路徑、沒有手動 Pattern，則改為自動載入 XML。舊版預設模式配方維持內建格式，套用時會清除原先未使用的 XML／Pattern 欄位，且不會因讀取而改寫原配方檔。

配方不包含輸出位置、報表或日誌本文。配方檔保存在目前使用者的設定目錄，重啟程式後仍可讀回：

- Windows：`%APPDATA%\java-log-analyzer\recipes.json`
- Linux／macOS：`$XDG_CONFIG_HOME/java-log-analyzer/recipes.json`；未設定時使用 `~/.config/java-log-analyzer/recipes.json`
- Windows 未設定 `APPDATA` 時，也使用使用者家目錄下的 `.config/java-log-analyzer/recipes.json`

若配方檔損壞、版本不支援或無法讀取，介面會顯示原因，不會自動清空或覆寫原檔。既有「上次使用條件」的儲存方式不變。

## CLI 使用範例

CLI 維持一次分析並匯出，不需經過 TUI 的匯出確認步驟；既有參數與預設全欄位輸出不變。來源可指定單一檔案或資料夾；執行前會列出來源模式、檔案數與完整檔名。支援 `.log`、`.txt`、`.out`（副檔名大小寫不拘），資料夾只掃描目前層，不遞迴子目錄。

### 基本分析

```bash
log-analyzer /path/to/logs
```

指定單一檔案時，只分析該檔案；父目錄中的其他日誌不會納入：

```bash
log-analyzer /path/to/application.LOG -f json -o report.json
```

指定的報表或分割檔已存在時，CLI 會停止並列出衝突路徑，不會等待互動輸入。可用 `-o` 換一個檔名；確定要取代本次產生的同名檔時，明確加入 `--overwrite`：

```bash
log-analyzer /path/to/application.LOG -f json -o report.json --overwrite
```

上述指令在安裝工具後的終端機執行，只影響指定的輸出檔案，不會修改系統設定。

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

CLI 使用相同可信度判斷，但不開互動選單。無法確認或不支援時會以非零狀態結束，不產生報表，並提示使用 `--pattern` 明確指定格式。`--pattern` 優先於 XML；未指定 XML 或手動 Pattern 時維持內建格式。

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

未使用 XML 或手動覆寫時，內建 Pattern 為：

```text
%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n
```

勾選「手動覆寫 Pattern」後可貼上 `logback.xml` 的 `<pattern>` 內容，但目前只支援常見 token；超出支援範圍時，工具會提示不支援的項目。CLI 仍可使用 `--log-pattern` 或 `--logback-xml`。

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

TUI 匯出設定可勾選附加證據清單，預設不勾選。工具會將報表與同一資料夾的 `<輸出檔名>_manifest.json` 一起準備完成後發布，清單記錄：

- 工具版本、產生時間，以及該次分析的固定條件與逐階計數
- 來源檔案的相對檔名、掃描時的大小／修改時間與穩定狀態
- 匯出時的來源核對狀態，以及核對通過的來源 SHA256
- 實際產生的報表檔名、大小與 SHA256，包含摘要及分割報表

來源若已變動、遺失、不可讀或無法安全核對，清單會標記原因，不會假裝驗證通過。報表仍使用已保留的分析結果。

這份清單不是日誌快照，也不能證明來源位元組與分析當時完全相同。來源核對依據是大小與奈秒修改時間，SHA256 則在匯出時才計算；即使核對通過，也有這個限制。清單不保存日誌本文或來源的絕對目錄，但分析條件與檔名仍可能含敏感資訊，分享前應先確認內容。

### 匯出失敗保護

報表、分割檔、摘要及勾選的證據清單會先寫入目的資料夾內的暫存區，全部準備完成後才發布。產生內容時失敗，不會取代舊報表；發布途中發生一般寫入錯誤時，工具會嘗試還原本次變更，並清除本次暫存。分析結果仍保留，可修正原因後重試。

若系統連還原都不允許，錯誤會列出未還原的檔案與備份目錄；請先保留該目錄，依訊息確認檔案後再處理。這項保護不保證斷電、程序被強制終止或多檔發布途中具備整組原子性。

若檔案已發布成功、只有暫存清理失敗，訊息會明列已發布檔案與殘留目錄；請依該訊息確認結果，不要將它當成尚未產生報表。

覆蓋只處理本次實際產生的檔案，不會自動刪除上次匯出留下的其他分割檔；完整輸出清單以本次摘要與完成訊息為準。來源日誌、目錄及符號連結等非一般檔案不能作為覆蓋目標。

## 效能量測

在專案根目錄、已啟用專案虛擬環境的終端機執行：

```text
python benchmarks/analysis_workflow.py --entries 20000 --repeat 3
```

程式會產生多檔、多群組及單群組大量重複三種合成資料，量測分析時間、Python 配置記憶體峰值、結果分頁、出現位置排序、原文定位與掃描中的取消反應；每輪也會核對事件、時間熱點與 Thread 的次數總和。

預設將 JSON 輸出至終端機，可用 `--output` 指定本地檔案。合成日誌與分析明細使用系統暫存目錄，結束後清理；不讀取使用者日誌、不修改系統設定。個別執行結果與量測筆記不加入版本控制。

比較前後結果時，請使用相同資料量、Python 版本與執行環境，多輪取中位數。分析時間包含 tracemalloc 的成本，記憶體數字是 Python 配置峰值，並非程序 RSS；取消延遲僅代表掃描階段。這些數字不保證所有來源、硬碟與終端機的效能，也不作為 CI 的固定時間門檻。

## 專案結構

- `src/log_analyzer/application/analysis_service.py`：分析流程與結果組裝
- `src/log_analyzer/application/evidence_manifest.py`：證據清單與來源／報表雜湊核對
- `src/log_analyzer/domain/parser.py`：核心解析邏輯
- `src/log_analyzer/infrastructure/exporting/`：匯出流程、欄位定義與 CSV／JSON／Markdown 實作
- `src/log_analyzer/infrastructure/recipe_store.py`：使用者本地命名配方儲存
- `src/log_analyzer/presentation/cli/`：CLI 進入點、參數定義與執行條件處理
- `src/log_analyzer/presentation/tui/app.py`：TUI 組裝入口、事件轉送與執行環境
- `src/log_analyzer/presentation/tui/composition.py`：表單與結果區的畫面組裝
- `src/log_analyzer/presentation/tui/controllers/`：來源選擇、Pattern、分析、匯出與結果狀態等操作流程
- `src/log_analyzer/presentation/tui/screens/`：路徑／檔案選擇、配方管理、事件瀏覽與確認對話框
- `src/log_analyzer/presentation/tui/components/`：共用輸入、來源預覽、結果提示及響應式元件
- `src/log_analyzer/presentation/tui/form_values.py`：日期輸入解析與表單預設值
- `src/log_analyzer/presentation/error_messages.py`：錯誤提示整理
- `src/log_analyzer/presentation/tui/recent_state.py`：最近一次表單狀態保存
- `src/log_analyzer/tcss/`：TUI 樣式檔
- `tests/domain/`：日誌解析、Pattern 與時間處理
- `tests/application/`：分析服務、來源預覽與證據清單
- `tests/infrastructure/`：匯出、路徑、命名與配方儲存
- `tests/presentation/cli/`、`tests/presentation/tui/`：兩種介面與操作流程
- `tests/integration/`：跨模組流程與套件相容入口
- `tests/fixtures/`：共用測試資料

## 適用環境

- Python 3.10+
- Windows / Linux / macOS

## 授權

MIT License. 請參閱 [LICENSE](LICENSE)
