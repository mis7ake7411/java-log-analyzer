import pytest

from log_analyzer.domain import parser
from log_analyzer.domain.parsing import aggregation, entries, scanner


def test_parser_keeps_its_public_parsing_entrypoints():
    """確認 parser 模組保留既有公開解析入口。"""
    assert callable(parser.parse_logs)
    assert callable(parser.ParseOptions)
    assert callable(parser.parse_log_directory)
    assert callable(parser.iter_logs)
    assert callable(parser.iter_sorted_logs)


def test_parsing_responsibilities_are_owned_by_their_modules():
    """確認掃描、事件組裝與彙整函式位於各自子模組。"""
    assert hasattr(entries, "_EntryAccumulator")
    assert hasattr(entries, "_parse_entry_time")
    assert hasattr(scanner, "iter_scanned_lines")
    assert hasattr(aggregation, "normalize_line_numbers")

    for name in (
        "_EntryAccumulator",
        "_parse_entry_time",
        "_iter_entries_from_file",
        "_record_line_coverage",
        "normalize_line_numbers",
    ):
        assert not hasattr(parser, name)


def _track_source_handles(monkeypatch):
    original_open = open
    opened = []

    class TrackedHandle:
        def __init__(self, *args, **kwargs):
            self.handle = original_open(*args, **kwargs)
            self.closed = False

        def __enter__(self):
            self.handle.__enter__()
            return self.handle

        def __exit__(self, *args):
            self.closed = True
            return self.handle.__exit__(*args)

    def tracked_open(*args, **kwargs):
        handle = TrackedHandle(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(scanner, "open", tracked_open, raising=False)
    return opened


def _write_two_log_entries(directory):
    (directory / "events.log").write_text(
        "2026-06-06 10:05:00.123 [main] INFO sample.Logger - first\n"
        "2026-06-06 10:05:01.123 [main] INFO sample.Logger - second\n",
        encoding="utf-8",
    )


def test_closing_parser_iterator_closes_the_open_source_file(tmp_path, monkeypatch):
    """確認提早關閉 parser iterator 會關閉來源日誌檔。"""
    _write_two_log_entries(tmp_path)
    opened = _track_source_handles(monkeypatch)

    iterator = parser.iter_logs(tmp_path)
    first_entry = next(iterator)

    assert first_entry["message"] == "first"
    assert len(opened) == 1
    assert not opened[0].closed

    iterator.close()

    assert opened[0].closed


def test_entry_assembly_error_closes_the_open_source_file(tmp_path, monkeypatch):
    """確認事件組裝失敗時掃描器會關閉來源日誌檔。"""
    _write_two_log_entries(tmp_path)
    opened = _track_source_handles(monkeypatch)

    def fail_after_reading_one_line(scanned_lines, accumulator):
        next(scanned_lines)
        raise RuntimeError("entry assembly failed")
        yield

    monkeypatch.setattr(entries, "iter_entries", fail_after_reading_one_line)
    iterator = parser.iter_logs(tmp_path)

    with pytest.raises(RuntimeError, match="entry assembly failed"):
        next(iterator)

    assert len(opened) == 1
    assert opened[0].closed
