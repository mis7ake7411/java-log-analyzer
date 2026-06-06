import os
import re
from datetime import datetime
from collections import Counter

# 標準的 Logback 格式正規表示式：2023-10-27 10:00:00.000 [thread] LEVEL logger - message
# 這個 Regex 會捕捉三個部分：時間戳記 (Timestamp)、日誌等級 (Level)、以及訊息內容 (Message)
LOG_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{3})?)\s+\[.*?\]\s+(\w+)\s+.*?\s+-\s+(.*)$')

def parse_logs(directory, start_time=None, end_time=None, keyword=None, ignore_case=False):
    """
    解析指定資料夾中的所有 Logback 日誌檔案。
    
    參數:
        directory (str): 包含 .log 檔案的資料夾路徑。
        start_time (datetime): 開始時間過濾條件。
        end_time (datetime): 結束時間過濾條件。
        keyword (str): 欲搜尋的關鍵字。
        ignore_case (bool): 是否忽略大小寫。
        
    回傳:
        tuple: (各等級的統計數量, 錯誤詳細資訊列表)
    """
    counts = Counter()
    errors = []
    
    if not os.path.exists(directory):
        raise FileNotFoundError(f"找不到目錄: {directory}")

    files = sorted([f for f in os.listdir(directory) if f.endswith('.log')])
    
    # 準備關鍵字過濾邏輯
    search_keyword = keyword
    if search_keyword and ignore_case:
        search_keyword = search_keyword.lower()

    for filename in files:
        filepath = os.path.join(directory, filename)
        current_error = None
        
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                match = LOG_PATTERN.match(line)
                
                if match:
                    if current_error:
                        # 在存入 ERROR 之前，檢查它（含 Stacktrace）是否符合關鍵字條件
                        if _should_include(current_error['message'] + current_error['stacktrace'], search_keyword, ignore_case):
                            errors.append(current_error)
                        current_error = None
                        
                    timestamp_str, level, message = match.groups()
                    
                    try:
                        dt = datetime.strptime(timestamp_str[:19], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        continue
                        
                    if start_time and dt < start_time:
                        continue
                    if end_time and dt > end_time:
                        continue
                    
                    # 如果不是 ERROR 等級，直接在這裡判斷關鍵字
                    if level.upper() != 'ERROR':
                        if _should_include(message, search_keyword, ignore_case):
                            counts[level] += 1
                    else:
                        # 如果是 ERROR，先建立物件，稍後合併 Stacktrace 再判斷
                        counts[level] += 1 # ERROR 計數不論關鍵字(方便統計總量)，或者您也可以改為符合關鍵字才計數
                        current_error = {
                            'timestamp': timestamp_str,
                            'level': level,
                            'message': message,
                            'stacktrace': ''
                        }
                else:
                    if current_error:
                        current_error['stacktrace'] += line
            
            # 處理檔案末尾的 ERROR
            if current_error:
                if _should_include(current_error['message'] + current_error['stacktrace'], search_keyword, ignore_case):
                    errors.append(current_error)
                
    return counts, errors

def _should_include(text, keyword, ignore_case):
    """
    內部輔助函式：判斷文字是否包含關鍵字。
    """
    if not keyword:
        return True
    
    target_text = text.lower() if ignore_case else text
    return keyword in target_text
