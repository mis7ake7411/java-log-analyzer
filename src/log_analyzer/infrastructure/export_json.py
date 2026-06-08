import json


def export_to_json(counts, matched_logs, output_path):
    """將結果匯出為 JSON 格式"""
    data = {
        'summary': dict(counts),
        'results': matched_logs
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
