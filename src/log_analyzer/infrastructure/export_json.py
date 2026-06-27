from __future__ import annotations

import json

from ..domain.parser_aggregation import normalize_line_numbers


def render_json_report(counts, matched_logs) -> str:
    """將分析結果渲染為 JSON 內容字串。"""
    data = {
        'summary': dict(counts),
        'results': [_log_to_dict(log) for log in matched_logs],
    }
    return json.dumps(data, indent=4, ensure_ascii=False)


def render_json_summary(counts, split_files=None) -> str:
    """渲染僅包含摘要的 JSON 內容字串。"""
    data = {
        'summary': dict(counts),
        'results': [],
        'split': {
            'enabled': True,
            'files': list(split_files or []),
        },
    }
    return json.dumps(data, indent=4, ensure_ascii=False)


def render_json_prefix(counts) -> str:
    """渲染 JSON 報表前綴，保留結果陣列開頭。"""
    summary = json.dumps(dict(counts), ensure_ascii=False)
    return '{\n    "summary": ' + summary + ',\n    "results": [\n'


def render_json_suffix() -> str:
    """渲染 JSON 報表結尾。"""
    return '\n    ]\n}\n'


def serialize_json_log(log, indent: int = 8) -> str:
    """渲染單筆 JSON 詳細項目。"""
    payload = _log_to_dict(log)
    rendered = json.dumps(payload, indent=4, ensure_ascii=False)
    prefix = " " * indent
    return "\n".join(f"{prefix}{line}" for line in rendered.splitlines())


def _log_to_dict(log):
    if hasattr(log, "to_dict"):
        return log.to_dict()

    payload = dict(log)
    payload["line_numbers"] = normalize_line_numbers(payload.get("line_numbers"))
    return payload
