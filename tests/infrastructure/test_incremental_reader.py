import pytest

from log_analyzer.infrastructure.sources.incremental import IncrementalFileReader


def test_reader_starts_at_zero_reads_in_bounded_batches_and_does_not_repeat(tmp_path):
    source = tmp_path / "app.log"
    source.write_bytes(b"first")
    reader = IncrementalFileReader(source)
    try:
        first = reader.read(max_bytes=2)
        assert (first.data, first.generation, first.reset, first.missing) == (
            b"fi",
            0,
            False,
            False,
        )
        assert reader.offset == 2
        assert reader.backlog == 3

        second = reader.read(max_bytes=8)
        assert second.data == b"rst"
        assert second.generation == 0
        assert reader.offset == 5
        assert reader.backlog == 0
        assert reader.read().data == b""
    finally:
        reader.close()


def test_missing_source_can_appear_without_skipping_its_first_generation(tmp_path):
    source = tmp_path / "late.log"
    reader = IncrementalFileReader(source)
    try:
        missing = reader.read()
        assert missing.data == b""
        assert missing.generation == 0
        assert missing.missing is True

        source.write_bytes(b"appeared")
        arrived = reader.read()
        assert arrived.data == b"appeared"
        assert arrived.generation == 0
        assert arrived.reset is False
        assert arrived.missing is False
    finally:
        reader.close()


def test_rotation_drains_old_handle_then_reports_reset_before_new_bytes(tmp_path):
    source = tmp_path / "app.log"
    rotated = tmp_path / "app.log.1"
    source.write_bytes(b"old-tail")
    reader = IncrementalFileReader(source)
    try:
        first = reader.read(max_bytes=3)
        assert first.data == b"old"

        source.replace(rotated)
        source.write_bytes(b"new-generation")

        drained = reader.read(max_bytes=16)
        assert drained.data == b"-tail"
        assert drained.generation == 0
        assert drained.reset is False

        reset = reader.read()
        assert reset.data == b""
        assert reset.generation == 1
        assert reset.reset is True
        assert reset.gap is None

        new_data = reader.read(max_bytes=4)
        assert new_data.data == b"new-"
        assert new_data.generation == 1
        assert new_data.reset is False
    finally:
        reader.close()


def test_truncation_starts_a_new_generation_and_reports_possible_loss(tmp_path):
    source = tmp_path / "app.log"
    source.write_bytes(b"old-content")
    reader = IncrementalFileReader(source)
    try:
        assert reader.read().data == b"old-content"
        source.write_bytes(b"new")

        reset = reader.read()
        assert reset.data == b""
        assert reset.generation == 1
        assert reset.reset is True
        assert reset.gap is not None

        assert reader.read().data == b"new"
        assert reader.offset == 3
    finally:
        reader.close()


def test_same_size_copytruncate_is_detected_by_bounded_content_anchors(tmp_path):
    source = tmp_path / "app.log"
    source.write_bytes(b"abcdefgh")
    reader = IncrementalFileReader(source)
    try:
        assert reader.read().data == b"abcdefgh"
        source.write_bytes(b"ABCDEFGH")

        reset = reader.read()
        assert reset.data == b""
        assert reset.generation == 1
        assert reset.reset is True
        assert "copytruncate" in reset.gap

        assert reader.read().data == b"ABCDEFGH"
    finally:
        reader.close()


def test_missing_path_still_drains_open_old_handle_before_retrying(tmp_path):
    source = tmp_path / "app.log"
    rotated = tmp_path / "app.log.1"
    source.write_bytes(b"old-content")
    reader = IncrementalFileReader(source)
    try:
        assert reader.read(max_bytes=3).data == b"old"
        source.replace(rotated)

        drained = reader.read()
        assert drained.data == b"-content"
        assert drained.missing is True
        assert drained.generation == 0

        source.write_bytes(b"restored")
        reset = reader.read()
        assert reset.data == b""
        assert reset.reset is True
        assert reset.generation == 1

        restored = reader.read()
        assert restored.data == b"restored"
        assert restored.generation == 1
    finally:
        reader.close()


def test_close_is_idempotent(tmp_path):
    source = tmp_path / "app.log"
    source.write_bytes(b"content")
    reader = IncrementalFileReader(source)
    reader.read(max_bytes=1)

    reader.close()
    reader.close()


def test_reader_releases_windows_delete_share_so_source_can_be_renamed(tmp_path):
    source = tmp_path / "app.log"
    rotated = tmp_path / "app.log.1"
    source.write_bytes(b"content")
    reader = IncrementalFileReader(source)
    try:
        reader.read(max_bytes=1)
        source.replace(rotated)
        assert rotated.read_bytes() == b"content"
    finally:
        reader.close()


class _TrackedHandle:
    def __init__(self, file_handle):
        self._file_handle = file_handle

    @property
    def closed(self):
        return self._file_handle.closed

    def fileno(self):
        return self._file_handle.fileno()

    def read(self, size=-1):
        return self._file_handle.read(size)

    def seek(self, offset, whence=0):
        return self._file_handle.seek(offset, whence)

    def close(self):
        self._file_handle.close()


def test_reader_caps_pending_rotations_and_closes_every_retained_handle(
    tmp_path,
    monkeypatch,
):
    pending_limit = 16
    source = tmp_path / "app.log"
    source.write_bytes(b"a" * 1024)
    reader = IncrementalFileReader(source)
    opened_handles = []
    original_open_source = reader._open_source

    def open_tracked_source():
        handle = _TrackedHandle(original_open_source())
        opened_handles.append(handle)
        return handle

    monkeypatch.setattr(reader, "_open_source", open_tracked_source)

    try:
        assert reader.read(max_bytes=1).data == b"a"
        for generation in range(1, pending_limit + 1):
            source.replace(tmp_path / f"app-{generation}.log")
            source.write_bytes(f"generation-{generation}".encode("utf-8"))
            batch = reader.read(max_bytes=1)
            assert batch.generation == 0
            assert batch.reset is False

        assert len(reader._candidates) == pending_limit
        assert reader.offset == pending_limit + 1

        source.replace(tmp_path / "app-overflow.log")
        source.write_bytes(b"overflow")
        with pytest.raises(OSError, match="16 pending source generations"):
            reader.read(max_bytes=1)

        assert reader.offset == pending_limit + 1
        assert len(reader._candidates) == pending_limit
    finally:
        reader.close()

    assert opened_handles
    assert all(handle.closed for handle in opened_handles)
