from .export_csv import export_to_csv
from .export_json import export_to_json
from .export_markdown import export_to_markdown

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
