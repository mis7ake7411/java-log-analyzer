import csv
import json

def export_results(counts, matched_logs, output_path, format='csv'):
    """
    根據指定的格式匯出分析結果。
    
    參數:
        counts (Counter): 統計數據。
        matched_logs (list): 符合條件的日誌詳情。
        output_path (str): 輸出路徑。
        format (str): 格式 ('csv', 'json', 'md')。
    """
    if format == 'json':
        export_to_json(counts, matched_logs, output_path)
    elif format == 'md':
        export_to_markdown(counts, matched_logs, output_path)
    else:
        export_to_csv(counts, matched_logs, output_path)

def export_to_csv(counts, matched_logs, output_path):
    """將結果匯出為 CSV 格式 (Excel 相容)"""
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['--- 統計摘要 ---'])
        writer.writerow(['日誌等級 (Level)', '出現次數 (Count)'])
        for level, count in sorted(counts.items()):
            writer.writerow([level, count])
        writer.writerow([]) 
        
        writer.writerow(['--- 詳細日誌內容 (已合併重複項) ---'])
        writer.writerow(['出現次數', '出現行號', '第一次時間', '最後一次時間', '等級', '線程', 'Logger', '來源檔案', '訊息內容', '堆疊追蹤 (含行號)'])
        for log in matched_logs:
            # 將行號列表轉為字串
            lines_str = ", ".join(map(str, log['line_numbers']))
            writer.writerow([
                log['count'],
                lines_str,
                log['timestamp'], 
                log['last_timestamp'],
                log['level'], 
                log['thread'], 
                log['logger'], 
                log['filename'],
                log['message'], 
                log['stacktrace'].strip()
            ])

def export_to_json(counts, matched_logs, output_path):
    """將結果匯出為 JSON 格式"""
    data = {
        'summary': dict(counts),
        'results': matched_logs
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def export_to_markdown(counts, matched_logs, output_path):
    """將結果匯出為 Markdown 格式"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# Java Log 分析報告\n\n")
        
        f.write("## 統計摘要\n")
        f.write("| 日誌等級 | 數量 |\n")
        f.write("| --- | --- |\n")
        for level, count in sorted(counts.items()):
            f.write(f"| {level} | {count} |\n")
        
        f.write("\n## 符合條件的詳細日誌\n")
        if not matched_logs:
            f.write("無符合條件的記錄。\n")
        else:
            for i, log in enumerate(matched_logs, 1):
                # 標題不再顯示次數，改為純訊息
                f.write(f"### {i}. [{log['level']}] {log['message']}\n")
                
                # 在細節中顯示行號
                lines_str = ", ".join(map(str, log['line_numbers']))
                f.write(f"- **出現行號**: `{lines_str}`\n")
                
                if log['count'] > 1:
                    f.write(f"- **時間範圍**: `{log['timestamp']}` ～ `{log['last_timestamp']}`\n")
                else:
                    f.write(f"- **出現時間**: `{log['timestamp']}`\n")
                    
                f.write(f"- **線程**: `{log['thread']}`\n")
                f.write(f"- **Logger**: `{log['logger']}`\n")
                f.write(f"- **來源檔案**: `{log['filename']}`\n")
                
                if log['stacktrace']:
                    f.write("#### 內容/堆疊追蹤 (含原始行號):\n")
                    f.write("```java\n")
                    f.write(log['stacktrace'].strip())
                    f.write("\n```\n")
                f.write("\n---\n")
