from __future__ import annotations

import json
import sys
from pathlib import Path
from collections.abc import Mapping

from ...infrastructure.recipe_store import get_recipe_store_path

_STATE_FILENAME = ".java-log-analyzer.tui-state.json"
_ALLOWED_KEYS = {
    "path",
    "output_path",
    "keyword",
    "ignore_case",
    "display_mode",
    "sort_by",
    "levels",
    "level_unrestricted",
    "split_by_level",
    "format",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
}


def get_recent_tui_state_path() -> Path:
    if getattr(sys, "frozen", False):
        return get_recipe_store_path().with_name("recent-state.json")
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return
