from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

_STATE_FILENAME = ".java-log-analyzer.tui-state.json"
_ALLOWED_KEYS = {
    "path",
    "output_path",
    "keyword",
    "ignore_case",
    "display_mode",
    "sort_by",
    "format",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
}


def get_recent_tui_state_path() -> Path:
    return Path.cwd() / _STATE_FILENAME


def load_recent_tui_state() -> dict[str, object]:
    path = get_recent_tui_state_path()
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    state: dict[str, object] = {}
    for key in _ALLOWED_KEYS:
        if key in raw:
            state[key] = raw[key]
    return state


def save_recent_tui_state(state: Mapping[str, object]) -> None:
    payload = {key: state[key] for key in _ALLOWED_KEYS if key in state}
    path = get_recent_tui_state_path()

    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return
