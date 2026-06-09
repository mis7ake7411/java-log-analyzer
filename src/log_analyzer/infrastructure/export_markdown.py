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
                f.write(f"### {i}. [{log['level']}] {log['message']}\n")

                lines_str = ", ".join(map(str, log['line_numbers']))
                f.write(f"- **出現行號**: `{lines_str}`\n")

                if log['count'] > 1:
                    f.write(f"- **時間範圍**: `{log['timestamp']}` ～ `{log['last_timestamp']}`\n")
                else:
                    f.write(f"- **出現時間**: `{log['timestamp']}`\n")

                f.write(f"- **線程**: `{log['thread']}`\n")
                f.write(f"- **Logger**: `{log['logger']}`\n")
                f.write(f"- **來源檔案**: `{log['filename']}`\n")

                if log.get('message_body'):
                    f.write("#### 延伸訊息:\n")
                    f.write("```text\n")
                    f.write(log['message_body'].rstrip())
                    f.write("\n```\n")

                if log['stacktrace']:
                    f.write("#### 內容/堆疊追蹤 (含原始行號):\n")
                    f.write("```java\n")
                    f.write(log['stacktrace'].strip())
                    f.write("\n```\n")
                f.write("\n---\n")
