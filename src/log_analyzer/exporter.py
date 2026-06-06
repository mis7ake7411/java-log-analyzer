import csv
import json

def export_results(counts, errors, output_path, format='csv'):
    """
    根據指定的格式匯出分析結果。
    
    參數:
        counts (Counter): 統計數據。
        errors (list): 錯誤詳情。
        output_path (str): 輸出路徑。
        format (str): 格式 ('csv', 'json', 'md')。
    """
    if format == 'json':
        export_to_json(counts, errors, output_path)
    elif format == 'md':
        export_to_markdown(counts, errors, output_path)
    else:
        export_to_csv(counts, errors, output_path)

def export_to_csv(counts, errors, output_path):
    """將結果匯出為 CSV 格式 (Excel 相容)"""
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['--- 摘要：各等級日誌計數 ---'])
        writer.writerow(['日誌等級 (Log Level)', '數量 (Count)'])
        for level, count in sorted(counts.items()):
            writer.writerow([level, count])
        writer.writerow([]) 
        writer.writerow(['--- 錯誤詳情與堆疊追蹤 (Stacktraces) ---'])
        writer.writerow(['時間戳記', '等級', '訊息內容', '堆疊追蹤內容'])
        for err in errors:
            writer.writerow([err['timestamp'], err['level'], err['message'], err['stacktrace'].strip()])

def export_to_json(counts, errors, output_path):
    """將結果匯出為 JSON 格式 (適合程式讀取)"""
    data = {
        'summary': dict(counts),
        'errors': errors
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def export_to_markdown(counts, errors, output_path):
    """將結果匯出為 Markdown 格式 (適合 GitHub/報告)"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# Java Log 分析報告\n\n")
        
        f.write("## 統計摘要\n")
        f.write("| 日誌等級 | 數量 |\n")
        f.write("| --- | --- |\n")
        for level, count in sorted(counts.items()):
            f.write(f"| {level} | {count} |\n")
        
        f.write("\n## 錯誤詳情\n")
        if not errors:
            f.write("無錯誤記錄。\n")
        else:
            for i, err in enumerate(errors, 1):
                f.write(f"### 錯誤 {i}: {err['message']}\n")
                f.write(f"- **時間**: {err['timestamp']}\n")
                f.write("- **等級**: ERROR\n")
                if err['stacktrace']:
                    f.write("#### 堆疊追蹤 (Stacktrace):\n")
                    f.write("```java\n")
                    f.write(err['stacktrace'].strip())
                    f.write("\n```\n\n")
                f.write("---\n")
