from pathlib import Path

from log_analyzer.application.source_context_index import SourceContextReader
from log_analyzer.application.source_preview import read_source_context


class _CountingTextFile:
    def __init__(self, file_handle) -> None:
        self._file_handle = file_handle
        self.readline_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._file_handle.close()

    def readline(self) -> str:
        self.readline_calls += 1
        return self._file_handle.readline()

    def seek(self, cookie: int) -> int:
        return self._file_handle.seek(cookie)

    def tell(self) -> int:
        return self._file_handle.tell()


def _snapshot(path: Path) -> dict[str, int | bool]:
    stat_result = path.stat()
    return {
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "stable": True,
    }


def _occurrence(filename: str, line_number: int) -> dict[str, str | int]:
    return {"filename": filename, "line_number": line_number}


def test_repeated_deep_previews_seek_from_sparse_offsets(tmp_path, monkeypatch) -> None:
    """情境：重複預覽深處原文應使用稀疏位元組索引定位。"""
    source_file = tmp_path / "deep.log"
    source_file.write_text(
        "".join(f"繁體中文第{line_number}行\n" for line_number in range(1, 12_001)),
        encoding="utf-8",
    )
    snapshot = _snapshot(source_file)
    opened_files: list[_CountingTextFile] = []
    original_open = Path.open

    def counted_open(path: Path, *args, **kwargs):
        file_handle = original_open(path, *args, **kwargs)
        if path.resolve() != source_file.resolve():
            return file_handle
        counted_file = _CountingTextFile(file_handle)
        opened_files.append(counted_file)
        return counted_file

    monkeypatch.setattr(Path, "open", counted_open)
    reader = SourceContextReader()

    def preview(line_number: int):
        opened_before = len(opened_files)
        result = read_source_context(
            str(tmp_path),
            {"filename": "deep.log", "line_number": line_number},
            snapshot,
            radius=2,
            reader=reader,
        )
        read_calls = (
            opened_files[-1].readline_calls if len(opened_files) > opened_before else 0
        )
        return result, read_calls

    first, first_reads = preview(9_000)
    nearby, nearby_reads = preview(9_020)
    backward, backward_reads = preview(4_000)

    assert first.warning is None
    assert first.lines == tuple(
        (line_number, f"繁體中文第{line_number}行")
        for line_number in range(8_998, 9_003)
    )
    assert first_reads == 9_002

    assert nearby.warning is None
    assert nearby.lines == tuple(
        (line_number, f"繁體中文第{line_number}行")
        for line_number in range(9_018, 9_023)
    )
    assert nearby_reads == 20
    assert nearby_reads <= 256 + 5

    assert backward.warning is None
    assert backward.lines == tuple(
        (line_number, f"繁體中文第{line_number}行")
        for line_number in range(3_998, 4_003)
    )
    assert backward_reads == 162
    assert backward_reads <= 256 + 5


def test_indexed_previews_keep_text_newline_and_boundary_semantics(tmp_path) -> None:
    """情境：使用索引讀取仍應保留文字、換行與邊界行為。"""
    source_file = tmp_path / "mixed-newlines.log"
    line_texts = [f"繁體中文第{line_number}行" for line_number in range(1, 601)]
    line_endings = ("\r\n", "\r", "\n")
    source_file.write_bytes(
        "".join(
            line_text + (line_endings[index % len(line_endings)] if index < 599 else "")
            for index, line_text in enumerate(line_texts)
        ).encode("utf-8")
    )
    snapshot = _snapshot(source_file)
    reader = SourceContextReader()

    middle = read_source_context(
        str(tmp_path), _occurrence("mixed-newlines.log", 258), snapshot, radius=1, reader=reader
    )
    first = read_source_context(
        str(tmp_path), _occurrence("mixed-newlines.log", 1), snapshot, radius=1, reader=reader
    )
    last = read_source_context(
        str(tmp_path), _occurrence("mixed-newlines.log", 600), snapshot, radius=1, reader=reader
    )
    missing = read_source_context(
        str(tmp_path), _occurrence("mixed-newlines.log", 601), snapshot, radius=0, reader=reader
    )

    assert middle.lines == tuple((line, line_texts[line - 1]) for line in (257, 258, 259))
    assert first.lines == tuple((line, line_texts[line - 1]) for line in (1, 2))
    assert last.lines == tuple((line, line_texts[line - 1]) for line in (599, 600))
    assert missing.lines == ()
    assert missing.warning is not None


def test_indexed_preview_strips_bom_and_replaces_invalid_utf8_without_losing_seek_context(tmp_path):
    """情境：索引讀取應移除 BOM 並替代無效 UTF-8 而不破壞定位。"""
    source_file = tmp_path / "invalid-utf8.log"
    source_file.write_bytes(
        b"\xef\xbb\xbffirst \x80\r\n"
        b"second \x81\r"
        b"third\n"
    )
    reader = SourceContextReader()

    preview = read_source_context(
        str(tmp_path), _occurrence("invalid-utf8.log", 2), _snapshot(source_file),
        radius=1, reader=reader,
    )

    assert preview.lines == (
        (1, "first \ufffd"),
        (2, "second \ufffd"),
        (3, "third"),
    )
    assert preview.warning is None
    assert preview.encoding_notice is not None


def test_indexed_preview_rejects_changed_source_and_rebuilds_for_new_snapshot(tmp_path) -> None:
    """情境：來源快照改變時應拒絕舊索引並為新快照重建。"""
    source_file = tmp_path / "changing.log"
    source_file.write_text(
        "".join(f"舊內容第{line_number}行\n" for line_number in range(1, 1_201)),
        encoding="utf-8",
    )
    old_snapshot = _snapshot(source_file)
    reader = SourceContextReader()

    initial = read_source_context(
        str(tmp_path), _occurrence("changing.log", 900), old_snapshot, reader=reader
    )
    source_file.write_text(
        "".join(f"新內容第{line_number}行\r" for line_number in range(1, 601)),
        encoding="utf-8",
    )

    stale = read_source_context(
        str(tmp_path), _occurrence("changing.log", 300), old_snapshot, reader=reader
    )
    fresh = read_source_context(
        str(tmp_path), _occurrence("changing.log", 300), _snapshot(source_file), reader=reader
    )

    assert initial.warning is None
    assert stale.lines == ()
    assert stale.warning is not None
    assert fresh.warning is None
    assert fresh.lines[3] == (300, "新內容第300行")


def test_cached_index_does_not_bypass_path_validation(tmp_path) -> None:
    """情境：命中索引快取時仍應驗證來源路徑。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    safe_file = log_dir / "safe.log"
    safe_file.write_text(
        "".join(f"safe-{line_number}\n" for line_number in range(1, 401)),
        encoding="utf-8",
    )
    outside_file = tmp_path / "outside.log"
    outside_file.write_text("外部機密\n", encoding="utf-8")
    reader = SourceContextReader()

    cached = read_source_context(
        str(log_dir), _occurrence("safe.log", 300), _snapshot(safe_file), reader=reader
    )
    traversal = read_source_context(
        str(log_dir), _occurrence("../outside.log", 1), _snapshot(outside_file), reader=reader
    )

    assert cached.warning is None
    assert traversal.lines == ()
    assert traversal.warning is not None


def test_reader_keeps_a_bounded_lru_of_source_offsets(tmp_path) -> None:
    """情境：來源索引快取應限制大小並淘汰最久未使用項目。"""
    reader = SourceContextReader()
    source_files = []
    for file_number in range(17):
        source_file = tmp_path / f"source-{file_number}.log"
        source_file.write_text(f"來源 {file_number}\n", encoding="utf-8")
        source_files.append(source_file)
        preview = read_source_context(
            str(tmp_path),
            _occurrence(source_file.name, 1),
            _snapshot(source_file),
            reader=reader,
        )
        assert preview.warning is None

    recent = read_source_context(
        str(tmp_path),
        _occurrence(source_files[0].name, 1),
        _snapshot(source_files[0]),
        reader=reader,
    )

    assert recent.warning is None
    assert len(reader._entries) == 16
    assert source_files[0].resolve() in reader._entries
    assert source_files[1].resolve() not in reader._entries
    assert source_files[-1].resolve() in reader._entries
