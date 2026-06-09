import csv


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
        writer.writerow(['出現次數', '出現行號', '第一次時間', '最後一次時間', '等級', '線程', 'Logger', '來源檔案', '訊息內容', '延伸訊息', '堆疊追蹤 (含行號)'])
        for log in matched_logs:
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
                log.get('message_body', '').strip(),
                log['stacktrace'].strip()
            ])
