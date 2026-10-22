from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

RECIPE_FIELDS = frozenset(
    {
        "path",
        "pattern_mode",
        "log_pattern",
        "logback_xml_path",
        "start_date",
        "start_time",
        "end_date",
        "end_time",
        "keyword",
        "ignore_case",
        "level_unrestricted",
        "levels",
        "sort_by",
        "display_mode",
    }
)

_STRING_FIELDS = RECIPE_FIELDS - {
    "ignore_case",
    "level_unrestricted",
    "levels",
}


def get_recipe_store_path() -> Path:
    """取得目前使用者的 named recipe 儲存路徑。"""
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "java-log-analyzer" / "recipes.json"
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config_home:
            return Path(xdg_config_home) / "java-log-analyzer" / "recipes.json"

    return Path.home() / ".config" / "java-log-analyzer" / "recipes.json"


def _normalize_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("配方名稱 name 必須是非空字串。")
    return name.strip()


def _normalize_state(state: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(state, Mapping):
        raise ValueError("配方狀態必須是映射（mapping）。")

    normalized: dict[str, object] = {}
    for field in RECIPE_FIELDS:
        if field not in state:
            continue

        value = state[field]
        if field in _STRING_FIELDS:
            if not isinstance(value, str):
                raise ValueError(f"欄位 {field!r} 必須是字串。")
            normalized[field] = value
        elif field in {"ignore_case", "level_unrestricted"}:
            if not isinstance(value, bool):
                raise ValueError(f"欄位 {field!r} 必須是布林值。")
            normalized[field] = value
        elif field == "levels":
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(level, str) for level in value
            ):
                raise ValueError(f"欄位 {field!r} 必須是字串清單或 tuple。")
            normalized[field] = list(value)

    return normalized


class RecipeStore:
    """以版本化 JSON 檔案保存多個分析表單 recipe。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else get_recipe_store_path()

    def list_recipes(self) -> dict[str, dict[str, object]]:
        """回傳所有 recipe；儲存檔無效時以 ValueError 說明原因。"""
        return self._read_recipes()

    def save(self, name: str, state: Mapping[str, object]) -> None:
        """新增或更新單一 recipe，並保留其他 recipe。"""
        recipe_name = _normalize_name(name)
        normalized_state = _normalize_state(state)
        recipes = self._read_recipes()
        recipes[recipe_name] = normalized_state
        self._write_recipes(recipes)

    def get(self, name: str) -> dict[str, object]:
        """取得指定 recipe；找不到時拋出 KeyError。"""
        recipe_name = _normalize_name(name)
        try:
            return self._read_recipes()[recipe_name]
        except KeyError:
            raise KeyError(f"找不到配方 {recipe_name!r}。") from None

    def delete(self, name: str) -> None:
        """刪除指定 recipe；不存在時不變更儲存檔。"""
        recipe_name = _normalize_name(name)
        recipes = self._read_recipes()
        if recipe_name not in recipes:
            return
        del recipes[recipe_name]
        self._write_recipes(recipes)

    def _read_recipes(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}

        try:
            content = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError(f"無法讀取配方檔案 {self.path}：{error}") from error
        except UnicodeError as error:
            raise ValueError(f"配方檔案 {self.path} 不是有效的 UTF-8：{error}") from error

        try:
            raw = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(f"配方檔案 {self.path} 的 JSON 格式錯誤：{error}") from error

        if not isinstance(raw, dict):
            raise ValueError(f"配方檔案 {self.path} 格式錯誤：根層必須是 JSON 物件。")
        if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
            raise ValueError(
                f"不支援配方檔案 {self.path} 的 schema_version："
                f"{raw.get('schema_version')!r}；目前只支援 1。"
            )

        stored_recipes = raw.get("recipes")
        if not isinstance(stored_recipes, dict):
            raise ValueError(f"配方檔案 {self.path} 格式錯誤：'recipes' 必須是 JSON 物件。")

        recipes: dict[str, dict[str, object]] = {}
        for stored_name, state in stored_recipes.items():
            if not isinstance(stored_name, str):
                raise ValueError(f"配方檔案 {self.path} 中的名稱 name 必須是字串。")
            try:
                recipe_name = _normalize_name(stored_name)
            except ValueError as error:
                raise ValueError(f"配方檔案 {self.path} 中的配方名稱無效：{error}") from error
            if recipe_name in recipes:
                raise ValueError(
                    f"配方檔案 {self.path} 中有重複配方名稱 {recipe_name!r}。"
                )
            try:
                recipes[recipe_name] = _normalize_state(state)
            except ValueError as error:
                raise ValueError(
                    f"配方 {recipe_name!r} 在 {self.path} 的狀態無效：{error}"
                ) from error

        return recipes

    def _write_recipes(self, recipes: dict[str, dict[str, object]]) -> None:
        payload = {"schema_version": 1, "recipes": recipes}
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(serialized)

            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
