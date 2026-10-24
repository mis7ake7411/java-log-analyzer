import pickle
from array import array

import pytest

from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.domain.matched_logs_store import MatchedLogsStore
from log_analyzer.domain.parser import ParseOptions, parse_log_directory
import log_analyzer.domain.parser as parser_module


def _write_entries(path, count):
    offsets = array("Q")
    with path.open("wb") as file_handle:
        for index in range(count):
            offsets.append(file_handle.tell())
            pickle.dump({"index": index}, file_handle)
    return offsets


def test_legacy_store_reuses_offsets_for_revisited_deep_page(tmp_path, monkeypatch):
    """情境：舊儲存器重訪深處分頁時應重用位元組索引。"""
    path = tmp_path / "matched.pkl"
    _write_entries(path, 100)
    store = MatchedLogsStore(str(path), 100)
    load_count = 0
    original_load = pickle.load

    def count_load(file_handle):
        nonlocal load_count
        load_count += 1
        return original_load(file_handle)

    monkeypatch.setattr(pickle, "load", count_load)

    assert [entry["index"] for entry in store.iter_page(70, 4)] == [70, 71, 72, 73]
    assert load_count == 74

    load_count = 0
    assert [entry["index"] for entry in store.iter_page(70, 4)] == [70, 71, 72, 73]
    assert load_count == 4


def test_legacy_random_access_builds_offsets_and_reuses_them(tmp_path, monkeypatch):
    """情境：舊儲存器隨機存取應建立並重用索引。"""
    path = tmp_path / "matched.pkl"
    _write_entries(path, 100)
    store = MatchedLogsStore(str(path), 100)
    load_count = 0
    original_load = pickle.load

    def count_load(file_handle):
        nonlocal load_count
        load_count += 1
        return original_load(file_handle)

    monkeypatch.setattr(pickle, "load", count_load)

    assert store[70] == {"index": 70}
    assert load_count == 71
    assert len(store._offsets) == 71
    assert list(store._offsets) == sorted(store._offsets)

    load_count = 0
    assert store[69] == {"index": 69}
    assert load_count == 1


def test_prebuilt_offsets_seek_to_pages_and_random_items(tmp_path, monkeypatch):
    """情境：預建索引應直接定位分頁與單筆資料。"""
    path = tmp_path / "matched.pkl"
    offsets = _write_entries(path, 100)
    store = MatchedLogsStore(str(path), 100, _offsets=offsets)
    load_count = 0
    original_load = pickle.load

    def count_load(file_handle):
        nonlocal load_count
        load_count += 1
        return original_load(file_handle)

    monkeypatch.setattr(pickle, "load", count_load)

    assert [entry["index"] for entry in store.iter_page(90, 3)] == [90, 91, 92]
    assert load_count == 3

    load_count = 0
    assert store[45] == {"index": 45}
    assert load_count == 1


def test_page_clamps_start_and_end_and_returns_empty_outside_bounds(tmp_path):
    """情境：分頁應修正範圍邊界且超界時產生空結果。"""
    path = tmp_path / "matched.pkl"
    offsets = _write_entries(path, 5)
    store = MatchedLogsStore(str(path), 5, _offsets=offsets)

    assert [entry["index"] for entry in store.iter_page(-3, 2)] == [0, 1]
    assert [entry["index"] for entry in store.iter_page(3, 8)] == [3, 4]
    assert list(store.iter_page(5, 2)) == []
    assert list(store.iter_page(0, 0)) == []
    assert list(store.iter_page(0, -1)) == []


def test_indexing_preserves_random_negative_and_invalid_index_behavior(tmp_path):
    """情境：索引讀取應保留隨機、負數與無效索引行為。"""
    path = tmp_path / "matched.pkl"
    offsets = _write_entries(path, 5)
    store = MatchedLogsStore(str(path), 5, _offsets=offsets)

    assert [store[index]["index"] for index in (0, 3, 1, -1, -5)] == [0, 3, 1, 4, 0]
    with pytest.raises(IndexError):
        store[5]
    with pytest.raises(IndexError):
        store[-6]


def test_short_pickle_file_stops_pages_and_invalidates_missing_item_access(tmp_path):
    """情境：pickle 檔案較短時分頁應停止且缺少單筆資料應報錯。"""
    path = tmp_path / "matched.pkl"
    _write_entries(path, 2)
    store = MatchedLogsStore(str(path), 4)

    assert list(store.iter_page(1, 3)) == [{"index": 1}]
    with pytest.raises(IndexError):
        store[3]


def test_random_access_cache_is_bounded_to_recent_entries(tmp_path):
    """情境：隨機讀取快取應限制為最近使用的少量項目。"""
    path = tmp_path / "matched.pkl"
    offsets = _write_entries(path, 100)
    store = MatchedLogsStore(str(path), 100, _offsets=offsets)

    for index in range(100):
        assert store[index] == {"index": index}

    assert len(store._index_cache) == 64
    assert list(store._index_cache) == list(range(36, 100))

    store[36]
    store[0]
    assert 36 in store._index_cache
    assert 37 not in store._index_cache


def test_constructor_preserves_existing_positional_fields_and_cache_entries(tmp_path, monkeypatch):
    """情境：建構子應保留舊版位置參數及既有快取項目。"""
    path = tmp_path / "matched.pkl"
    offsets = _write_entries(path, 70)
    supplied_cache = {index: {"cached_index": index} for index in range(70)}
    source_states = {"app.log": {"size": 1, "mtime_ns": 2, "stable": True}}
    diagnostics = ScanDiagnostics()
    store = MatchedLogsStore(
        str(path),
        70,
        supplied_cache,
        source_states,
        diagnostics,
        offsets,
    )
    load_count = 0
    original_load = pickle.load

    def count_load(file_handle):
        nonlocal load_count
        load_count += 1
        return original_load(file_handle)

    monkeypatch.setattr(pickle, "load", count_load)

    assert store.source_states is source_states
    assert store.diagnostics is diagnostics
    assert store._index_cache is supplied_cache
    assert len(store._index_cache) == 64
    assert store[69] == {"cached_index": 69}
    assert load_count == 0
    assert store._offsets.typecode == "Q"


def test_store_keeps_iterable_truth_and_list_equality_behavior(tmp_path):
    """情境：儲存器應保留迭代、真假值及與 list 比較行為。"""
    path = tmp_path / "matched.pkl"
    _write_entries(path, 3)
    store = MatchedLogsStore(str(path), 3)

    assert bool(store)
    assert store == [{"index": 0}, {"index": 1}, {"index": 2}]
    assert list(store) == [{"index": 0}, {"index": 1}, {"index": 2}]

    empty_path = tmp_path / "empty.pkl"
    _write_entries(empty_path, 0)
    empty_store = MatchedLogsStore(str(empty_path), 0)
    assert not empty_store
    assert empty_store == []


def test_release_is_idempotent_after_partial_indexing(tmp_path):
    """情境：部分建立索引後重複釋放應安全。"""
    path = tmp_path / "matched.pkl"
    _write_entries(path, 10)
    store = MatchedLogsStore(str(path), 10)

    assert store[4] == {"index": 4}
    assert len(store._offsets) == 5
    store.release()
    store.release()

    assert not path.exists()
    assert store._index_cache == {}
    assert store._offsets is None


def test_parse_result_uses_persisted_offsets_for_first_deep_page(tmp_path, monkeypatch):
    """情境：解析結果的第一個深處分頁應直接使用保存的索引。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lines = []
    for index in range(100):
        minute, second = divmod(index, 60)
        lines.append(
            f"2026-06-06 10:{minute:02d}:{second:02d}.000 "
            f"[main] INFO  com.test - Entry {index}\n"
        )
    (log_dir / "app.log").write_text("".join(lines), encoding="utf-8")

    counts, store = parse_log_directory(str(log_dir), ParseOptions())
    assert counts == {"INFO": 100}
    assert len(store) == 100

    load_count = 0
    original_load = parser_module.pickle.load

    def count_load(file_handle):
        nonlocal load_count
        load_count += 1
        return original_load(file_handle)

    monkeypatch.setattr(parser_module.pickle, "load", count_load)

    page = list(store.iter_page(70, 4))

    assert [entry["message"] for entry in page] == [
        "Entry 70",
        "Entry 71",
        "Entry 72",
        "Entry 73",
    ]
    assert load_count == 4
