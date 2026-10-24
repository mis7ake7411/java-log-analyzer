import json

import pytest

import log_analyzer.infrastructure.recipe_store as recipe_store
from log_analyzer.infrastructure.recipe_store import RecipeStore, get_recipe_store_path


def test_saves_multiple_recipes_and_reads_them_after_restart(tmp_path):
    """情境：多個配方儲存後重開應仍能讀取。"""
    path = tmp_path / "recipes.json"
    store = RecipeStore(path)
    first_state = {
        "path": "./logs/服務.log",
        "pattern_mode": "custom",
        "log_pattern": "%d %-5level %logger - %msg%n",
        "logback_xml_path": "./config/logback.xml",
        "start_date": "2026-10-01",
        "start_time": "08:30",
        "end_date": "2026-10-02",
        "end_time": "09:45",
        "keyword": "例外",
        "ignore_case": True,
        "level_unrestricted": False,
        "levels": ("ERROR", "WARN"),
        "sort_by": "time",
        "display_mode": "summary",
    }
    second_state = {
        "path": "./other.log",
        "keyword": "timeout",
        "ignore_case": False,
        "level_unrestricted": True,
        "levels": (),
    }

    store.save("服務問題", first_state)
    store.save("Timeouts", second_state)

    restarted = RecipeStore(path)
    assert restarted.get("服務問題") == {
        "path": "./logs/服務.log",
        "pattern_mode": "custom",
        "log_pattern": "%d %-5level %logger - %msg%n",
        "logback_xml_path": "./config/logback.xml",
        "start_date": "2026-10-01",
        "start_time": "08:30",
        "end_date": "2026-10-02",
        "end_time": "09:45",
        "keyword": "例外",
        "ignore_case": True,
        "level_unrestricted": False,
        "levels": ["ERROR", "WARN"],
        "sort_by": "time",
        "display_mode": "summary",
    }
    assert restarted.get("Timeouts") == {
        "path": "./other.log",
        "keyword": "timeout",
        "ignore_case": False,
        "level_unrestricted": True,
        "levels": [],
    }


def test_overwriting_a_recipe_preserves_other_recipes(tmp_path):
    """情境：覆寫指定配方應保留其他配方。"""
    store = RecipeStore(tmp_path / "recipes.json")
    store.save("first", {"keyword": "old"})
    store.save("second", {"keyword": "keep"})

    store.save(" first ", {"keyword": "new"})

    assert store.list_recipes() == {
        "first": {"keyword": "new"},
        "second": {"keyword": "keep"},
    }


def test_delete_persists_and_only_removes_the_named_recipe(tmp_path):
    """情境：刪除應持久保存且只移除指定配方。"""
    path = tmp_path / "recipes.json"
    store = RecipeStore(path)
    store.save("first", {"keyword": "remove"})
    store.save("second", {"keyword": "keep"})

    store.delete("first")

    assert RecipeStore(path).list_recipes() == {"second": {"keyword": "keep"}}
    store.delete("missing")
    assert RecipeStore(path).list_recipes() == {"second": {"keyword": "keep"}}


def test_missing_file_has_no_recipes_and_get_raises_key_error(tmp_path):
    """情境：配方檔不存在時列空清單且取得名稱應拋 KeyError。"""
    store = RecipeStore(tmp_path / "missing.json")

    assert store.list_recipes() == {}
    with pytest.raises(KeyError) as error:
        store.get("missing")
    assert "找不到配方" in str(error.value)
    assert "missing" in str(error.value)


def test_unknown_fields_are_ignored_even_when_their_values_are_not_json_values(tmp_path):
    """情境：未知欄位應忽略即使其值無法 JSON 序列化。"""
    store = RecipeStore(tmp_path / "recipes.json")
    store.save(
        "safe",
        {
            "path": "./logs",
            "keyword": "failure",
            "output_path": tmp_path,
            "output_name": "report",
            "format": "json",
            "raw_log": "secret log contents",
        },
    )

    assert store.get("safe") == {"path": "./logs", "keyword": "failure"}


@pytest.mark.parametrize(
    ("state", "field"),
    [
        ({"path": 12}, "path"),
        ({"ignore_case": "yes"}, "ignore_case"),
        ({"level_unrestricted": 1}, "level_unrestricted"),
        ({"levels": ["ERROR", 3]}, "levels"),
        ({"levels": "ERROR"}, "levels"),
        ([], "mapping"),
    ],
    ids=[
        'path-not-string',
        'ignore-case-not-bool',
        'unrestricted-not-bool',
        'mixed-level-types',
        'levels-not-list',
        'state-not-mapping',
    ],
)
def test_save_rejects_invalid_state_types(tmp_path, state, field):
    """情境：配方欄位型別不正確時應拒絕保存。"""
    store = RecipeStore(tmp_path / "recipes.json")

    with pytest.raises(ValueError) as error:
        store.save("invalid", state)
    assert "必須" in str(error.value)
    assert field in str(error.value)

    assert not (tmp_path / "recipes.json").exists()


@pytest.mark.parametrize("name", ["", "   ", None], ids=['empty-name', 'whitespace-name', 'none-name'])
def test_save_rejects_empty_or_non_string_names(tmp_path, name):
    """情境：空白或非字串配方名稱應拒絕。"""
    store = RecipeStore(tmp_path / "recipes.json")

    with pytest.raises(ValueError) as error:
        store.save(name, {"keyword": "failure"})
    assert "配方名稱" in str(error.value)
    assert "name" in str(error.value)

    assert not (tmp_path / "recipes.json").exists()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not-json", "JSON 格式"),
        ('{"schema_version": 2, "recipes": {}}', "不支援"),
        ('{"schema_version": true, "recipes": {}}', "schema_version"),
        ('{"schema_version": 1, "recipes": []}', "recipes"),
        ('{"schema_version": 1, "recipes": {"broken": {"ignore_case": "yes"}}}', "ignore_case"),
    ],
    ids=[
        'malformed-json',
        'unsupported-schema',
        'boolean-schema',
        'recipes-not-mapping',
        'invalid-recipe-field',
    ],
)
def test_list_rejects_corrupt_or_invalid_recipe_files(tmp_path, content, message):
    """情境：損壞 JSON、無效 schema 或欄位應回報明確錯誤。"""
    path = tmp_path / "recipes.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError) as error:
        RecipeStore(path).list_recipes()
    assert message in str(error.value)


@pytest.mark.parametrize(
    ("content", "operation"),
    [
        ("{broken", "save"),
        ('{"schema_version": 2, "recipes": {}}', "delete"),
    ],
    ids=['save-malformed-json', 'delete-unsupported-schema'],
)
def test_failed_read_never_overwrites_corrupt_file_on_save_or_delete(
    tmp_path, content, operation
):
    """情境：既有檔案讀取失敗後保存或刪除不得覆寫損壞檔。"""
    path = tmp_path / "recipes.json"
    original_bytes = content.encode("utf-8")
    path.write_bytes(original_bytes)
    store = RecipeStore(path)

    with pytest.raises(ValueError):
        if operation == "save":
            store.save("new", {"keyword": "failure"})
        else:
            store.delete("old")

    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("operation", ["list", "save", "delete"], ids=['operation-list', 'operation-save', 'operation-delete'])
def test_unreadable_file_reports_access_failure_without_overwrite(
    tmp_path, monkeypatch, operation
):
    """情境：配方不可讀時各操作應回報存取失敗且不覆蓋。"""
    path = tmp_path / "recipes.json"
    original_bytes = b'{"schema_version": 1, "recipes": {"existing": {}}}'
    path.write_bytes(original_bytes)
    store = RecipeStore(path)
    original_read_text = recipe_store.Path.read_text

    def fail_read(target, *args, **kwargs):
        if target == path:
            raise PermissionError("access denied by test")
        return original_read_text(target, *args, **kwargs)

    monkeypatch.setattr(recipe_store.Path, "read_text", fail_read)

    with pytest.raises(ValueError) as error:
        if operation == "list":
            store.list_recipes()
        elif operation == "save":
            store.save("new", {"keyword": "failure"})
        else:
            store.delete("existing")

    assert f"無法讀取配方檔案 {path}" in str(error.value)
    assert "access denied by test" in str(error.value)
    assert "JSON 格式" not in str(error.value)
    assert path.read_bytes() == original_bytes


def test_recipe_json_uses_utf8_and_preserves_unicode_values(tmp_path):
    """情境：配方 JSON 應使用 UTF-8 並保留 Unicode。"""
    path = tmp_path / "recipes.json"
    store = RecipeStore(path)

    store.save("資料庫錯誤", {"keyword": "連線逾時 🚦"})

    assert "資料庫錯誤" in path.read_text(encoding="utf-8")
    assert "連線逾時 🚦" in path.read_text(encoding="utf-8")
    assert RecipeStore(path).get("資料庫錯誤") == {"keyword": "連線逾時 🚦"}


def test_default_path_uses_appdata_on_windows(tmp_path, monkeypatch):
    """情境：Windows 預設配方路徑應使用 APPDATA。"""
    appdata = tmp_path / "roaming"
    monkeypatch.setattr(recipe_store.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert get_recipe_store_path() == appdata / "java-log-analyzer" / "recipes.json"


def test_default_path_uses_xdg_config_home_outside_windows(tmp_path, monkeypatch):
    """情境：非 Windows 預設路徑應使用 XDG_CONFIG_HOME。"""
    xdg_home = tmp_path / "xdg"
    monkeypatch.setattr(recipe_store.sys, "platform", "linux")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_home))

    actual = get_recipe_store_path()
    expected = xdg_home / "java-log-analyzer" / "recipes.json"
    assert actual == expected, f"actual={actual!r}, expected={expected!r}"


def test_default_path_falls_back_to_home_config_directory(tmp_path, monkeypatch):
    """情境：沒有平台設定時應退回家目錄的 .config。"""
    home = tmp_path / "home"
    monkeypatch.setattr(recipe_store.sys, "platform", "linux")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(recipe_store.Path, "home", lambda: home)

    assert get_recipe_store_path() == home / ".config" / "java-log-analyzer" / "recipes.json"


def test_replace_failure_preserves_existing_file_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    """情境：原子替換失敗應保留舊配方並清理暫存檔。"""
    path = tmp_path / "nested" / "recipes.json"
    store = RecipeStore(path)
    store.save("existing", {"keyword": "original"})
    original_bytes = path.read_bytes()

    def fail_replace(source, destination):
        assert source.parent == destination.parent == path.parent
        assert destination == path
        raise OSError("atomic replace failed")

    monkeypatch.setattr(recipe_store.os, "replace", fail_replace)

    with pytest.raises(OSError, match="atomic replace failed"):
        store.save("new", {"keyword": "replacement"})

    assert path.read_bytes() == original_bytes
    assert list(path.parent.iterdir()) == [path]


def test_recipe_file_has_versioned_public_schema(tmp_path):
    """情境：配方檔應使用含版本號的公開 schema。"""
    path = tmp_path / "recipes.json"

    RecipeStore(path).save("named", {"keyword": "failure"})

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "recipes": {"named": {"keyword": "failure"}},
    }
