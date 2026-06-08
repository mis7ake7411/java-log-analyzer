import os
from datetime import datetime
from collections import Counter

from .logback_pattern import DEFAULT_LOGBACK_REGEX, compile_logback_pattern

LEVEL_SORT_ORDER = {
    "ERROR": 0,
    "WARN": 1,
    "WARNING": 1,
    "INFO": 2,
    "DEBUG": 3,
    "TRACE": 4,
}


def parse_logs(
    directory,
    start_time=None,
    end_time=None,
    keyword=None,
    ignore_case=False,
    log_pattern=None,
    sort_by="time",
):
    """
    解析指定資料夾中的所有 Logback 日誌檔案。
    
    回傳:
        tuple: (各等級的統計數量, 所有符合條件的日誌詳情列表)
    """
    entry_pattern = compile_logback_pattern(log_pattern) if log_pattern else DEFAULT_LOGBACK_REGEX
    counts = Counter()
    matched_logs = []
    
    if not os.path.exists(directory):
        raise FileNotFoundError(f"找不到目錄: {directory}")

    files = sorted([f for f in os.listdir(directory) if f.endswith('.log')])
    
    # 準備關鍵字過濾邏輯
    search_keyword = keyword
    if search_keyword and ignore_case:
        search_keyword = search_keyword.lower()

    # 使用字典來暫存，key 為 (level, logger, message, stacktrace)，value 為該組的詳情
    grouped_logs = {}

    for filename in files:
        filepath = os.path.join(directory, filename)
        current_entry = None
        
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                match = entry_pattern.match(line)
                
                if match:
                    if current_entry:
                        _commit_entry(grouped_logs, counts, current_entry, search_keyword, ignore_case)
                        current_entry = None
                        
                    timestamp_str = match.group("timestamp")
                    thread = match.group("thread")
                    level = match.group("level")
                    logger = match.group("logger")
                    message = match.group("message")
                    
                    try:
                        dt = datetime.strptime(timestamp_str[:19], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        continue
                        
                    if start_time and dt < start_time:
                        continue
                    if end_time and dt > end_time:
                        continue
                    
                    current_entry = {
                        'timestamp': timestamp_str,
                        'thread': thread,
                        'level': level,
                        'logger': logger,
                        'message': message,
                        'filename': filename,
                        'line_num': line_num,
                        'stacktrace_lines': [],
                        'full_text': message,
                        'line_numbers': [line_num] # 追蹤所有重複項出現的行號
                    }
                else:
                    if current_entry:
                        # 幫 Stacktrace 的每一行標註行號
                        current_entry['stacktrace_lines'].append(f"{line_num:5}: {line}")
                        current_entry['full_text'] += line
            
            if current_entry:
                _commit_entry(grouped_logs, counts, current_entry, search_keyword, ignore_case)
                
    # 將 dict 轉回 list 並排序
    final_logs = sorted(grouped_logs.values(), key=lambda x: _sort_key(x, sort_by))
    return counts, final_logs


def _sort_key(entry, sort_by):
    timestamp = entry["timestamp"]
    if sort_by == "level":
        return (
            LEVEL_SORT_ORDER.get(entry["level"], 99),
            timestamp,
        )
    return (timestamp,)

def _commit_entry(grouped_logs, counts, entry, keyword, ignore_case):
    """
    內部輔助函式：套用文字過濾後，才將 log 納入統計與分組。
    """
    if not _should_include(entry['full_text'], keyword, ignore_case):
        return

    counts[entry['level']] += 1
    _add_to_grouped_logs(grouped_logs, entry)

def _add_to_grouped_logs(grouped_logs, entry):
    """
    內部輔助函式：將新的 log 加入分組中。
    """
    stacktrace_text = ''.join(entry['stacktrace_lines']).strip()
    key = (entry['level'], entry['logger'], entry['message'], stacktrace_text)
    
    if key in grouped_logs:
        grouped_logs[key]['count'] += 1
        grouped_logs[key]['last_timestamp'] = entry['timestamp']
        # 記錄重複項出現的行號
        grouped_logs[key]['line_numbers'].append(entry['line_num'])
    else:
        entry['count'] = 1
        entry['last_timestamp'] = entry['timestamp']
        entry['stacktrace'] = stacktrace_text
        grouped_logs[key] = entry

def _should_include(text, keyword, ignore_case):
    """
    內部輔助函式：判斷文字是否包含關鍵字。
    """
    if not keyword:
        return True
    
    target_text = text.lower() if ignore_case else text
    return keyword in target_text
