import os
from pathlib import Path

import pytest

from log_analyzer.infrastructure.exporting.transaction import (
    ExportConflictError,
    ExportRecoveryError,
    ExportTransaction,
)


def _stage_files(transaction, files):
    staged = []
    for name, content in files.items():
        path = transaction.stage_dir / name
        path.write_bytes(content)
        staged.append(path)
    return staged


def test_no_clobber_conflict_reports_all_existing_targets_without_publishing(tmp_path):
    """情境：不覆蓋模式應列出全部既有目標且不發布檔案。"""
    output = tmp_path / "report.csv"
    (tmp_path / "first.csv").write_bytes(b"old first")
    (tmp_path / "second.csv").write_bytes(b"old second")
    transaction = ExportTransaction(output)
    staged = _stage_files(transaction, {"first.csv": b"new first", "second.csv": b"new second"})

    try:
        with pytest.raises(ExportConflictError) as error:
            transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert set(error.value.paths) == {
        str((tmp_path / "first.csv").resolve()),
        str((tmp_path / "second.csv").resolve()),
    }
    assert (tmp_path / "first.csv").read_bytes() == b"old first"
    assert (tmp_path / "second.csv").read_bytes() == b"old second"
    assert not list(tmp_path.glob(".report.csv.export-*"))


@pytest.mark.parametrize("target_kind", ["directory", "symlink"], ids=['target-kind-directory', 'target-kind-symlink'])
def test_export_target_must_be_a_regular_file_or_absent(tmp_path, target_kind):
    """情境：目標是目錄或符號連結時應拒絕替換。"""
    output = tmp_path / "report.csv"
    target = tmp_path / "first.csv"
    if target_kind == "directory":
        target.mkdir()
    else:
        source = tmp_path / "source.csv"
        source.write_bytes(b"source")
        try:
            target.symlink_to(source)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"symlinks unavailable: {error}")

    transaction = ExportTransaction(output)
    staged = _stage_files(transaction, {"first.csv": b"new first"})
    try:
        with pytest.raises(OSError):
            transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert target.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows paths are case-insensitive")
def test_case_distinct_protected_source_does_not_block_export_on_posix(tmp_path):
    """情境：POSIX 上僅大小寫不同的來源不應誤擋匯出。"""
    source = tmp_path / "Report.csv"
    output = tmp_path / "report.csv"
    source.write_bytes(b"input source")
    transaction = ExportTransaction(output, protected_paths=(source,))
    staged = _stage_files(transaction, {"report.csv": b"export output"})

    try:
        transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert source.read_bytes() == b"input source"
    assert output.read_bytes() == b"export output"


@pytest.mark.skipif(os.name == "nt", reason="Windows paths are case-insensitive")
def test_case_distinct_targets_are_allowed_on_posix(tmp_path):
    """情境：POSIX 上僅大小寫不同的目標應可並存。"""
    transaction = ExportTransaction(tmp_path / "report.csv")
    staged = _stage_files(
        transaction,
        {"report_ERROR.csv": b"upper", "report_error.csv": b"lower"},
    )

    try:
        transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert (tmp_path / "report_ERROR.csv").read_bytes() == b"upper"
    assert (tmp_path / "report_error.csv").read_bytes() == b"lower"


def test_later_publish_failure_rolls_back_new_files_and_restores_overwritten_files(
    tmp_path, monkeypatch
):
    """情境：後續發布失敗應回滾新檔並還原被覆蓋檔案。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    old_files = {
        tmp_path / "first.csv": b"old first",
        tmp_path / "second.csv": b"old second",
    }
    for path, content in old_files.items():
        path.write_bytes(content)
    transaction = ExportTransaction(tmp_path / "report.csv", overwrite=True)
    staged = _stage_files(transaction, {"first.csv": b"new first", "second.csv": b"new second"})
    original_publish = transaction_module._publish_no_clobber

    def fail_second_publish(source, destination):
        if Path(destination).name == "second.csv":
            raise OSError("simulated later publish failure")
        return original_publish(source, destination)

    monkeypatch.setattr(transaction_module, "_publish_no_clobber", fail_second_publish)

    try:
        with pytest.raises(OSError, match="simulated later publish failure"):
            transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert {path: path.read_bytes() for path in old_files} == old_files
    assert not list(tmp_path.glob(".report.csv.export-*"))


def test_interrupt_immediately_after_backup_restores_original_file(tmp_path, monkeypatch):
    """情境：剛備份完成就中斷時應還原原檔。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    original = tmp_path / "first.csv"
    original.write_bytes(b"old bytes")
    transaction = ExportTransaction(tmp_path / "report.csv", overwrite=True)
    staged = _stage_files(transaction, {"first.csv": b"new bytes"})
    real_replace = transaction_module.os.replace

    def interrupt_after_move(source, destination, *args, **kwargs):
        real_replace(source, destination, *args, **kwargs)
        if "backups" in Path(destination).parts:
            raise KeyboardInterrupt("after backup move")

    monkeypatch.setattr(transaction_module.os, "replace", interrupt_after_move)

    try:
        with pytest.raises(KeyboardInterrupt, match="after backup move"):
            transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert original.read_bytes() == b"old bytes"
    assert not list(tmp_path.glob(".report.csv.export-*"))


def test_interrupt_after_first_publish_rolls_back_to_original(tmp_path, monkeypatch):
    """情境：第一個檔案發布後中斷應回復原狀。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    original = tmp_path / "first.csv"
    original.write_bytes(b"old bytes")
    transaction = ExportTransaction(tmp_path / "report.csv", overwrite=True)
    staged = _stage_files(transaction, {"first.csv": b"new bytes"})
    real_publish = transaction_module._publish_no_clobber

    def publish_then_interrupt(source, destination):
        real_publish(source, destination)
        raise KeyboardInterrupt("after publication")

    monkeypatch.setattr(transaction_module, "_publish_no_clobber", publish_then_interrupt)

    try:
        with pytest.raises(KeyboardInterrupt, match="after publication"):
            transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert original.read_bytes() == b"old bytes"
    assert not list(tmp_path.glob(".report.csv.export-*"))


def test_identity_is_captured_before_publication_without_post_publish_stat(
    tmp_path, monkeypatch
):
    """情境：檔案識別應在發布前取得且不依賴發布後 stat。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    transaction = ExportTransaction(tmp_path / "report.csv")
    staged = _stage_files(transaction, {"first.csv": b"new bytes"})
    real_identity = transaction_module._file_identity
    published = False
    real_publish = transaction_module._publish_no_clobber

    def publish_and_mark(source, destination):
        nonlocal published
        real_publish(source, destination)
        published = True

    def identity_before_publish(path):
        if published:
            raise OSError("stat after publish must not be needed")
        return real_identity(path)

    monkeypatch.setattr(transaction_module, "_file_identity", identity_before_publish)
    monkeypatch.setattr(transaction_module, "_publish_no_clobber", publish_and_mark)

    try:
        assert transaction.publish(staged) == [str(tmp_path / "first.csv")]
    finally:
        transaction.cleanup()

    assert (tmp_path / "first.csv").read_bytes() == b"new bytes"


def test_interrupted_rollback_preserves_backup_and_reports_recovery_paths(
    tmp_path, monkeypatch
):
    """情境：回滾途中再次中斷應保留備份並提供復原路徑。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    original = tmp_path / "first.csv"
    original.write_bytes(b"old bytes")
    transaction = ExportTransaction(tmp_path / "report.csv", overwrite=True)
    staged = _stage_files(
        transaction,
        {"first.csv": b"new bytes", "second.csv": b"second new bytes"},
    )
    real_publish = transaction_module._publish_no_clobber

    def fail_second_publish(source, destination):
        if Path(destination).name == "second.csv":
            raise OSError("first interrupt during publication")
        return real_publish(source, destination)

    def interrupt_rollback(*_args, **_kwargs):
        raise KeyboardInterrupt("second interrupt during rollback")

    monkeypatch.setattr(transaction_module, "_publish_no_clobber", fail_second_publish)
    monkeypatch.setattr(transaction, "_rollback", interrupt_rollback)

    with pytest.raises(ExportRecoveryError) as error:
        transaction.publish(staged)

    recovery_dir = Path(error.value.recovery_dir)
    assert recovery_dir.is_dir()
    assert (recovery_dir / "backups" / "0000-first.csv").read_bytes() == b"old bytes"
    assert str(original.resolve()) in error.value.unrestored_paths
    transaction.cleanup()
    assert recovery_dir.exists()


def test_cleanup_failure_is_visible_and_preserves_stage_with_published_paths(
    tmp_path, monkeypatch
):
    """情境：清理失敗應明確回報且保留已發布路徑與暫存。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    transaction = ExportTransaction(tmp_path / "report.csv")
    staged = _stage_files(transaction, {"first.csv": b"new bytes"})
    published = transaction.publish(staged)

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(transaction_module.shutil, "rmtree", fail_cleanup)

    with pytest.raises(OSError) as error:
        transaction.cleanup()

    assert published[0] in str(error.value)
    assert str(transaction.stage_dir.resolve()) in str(error.value)
    assert str(error.value.__cause__) == "simulated cleanup failure"
    assert transaction.stage_dir.exists()


def test_no_clobber_race_after_first_publish_rolls_back_only_our_file(tmp_path, monkeypatch):
    """情境：發布後出現同名競爭檔案時只應回滾本次建立的檔案。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    transaction = ExportTransaction(tmp_path / "report.csv")
    staged = _stage_files(transaction, {"first.csv": b"new first", "second.csv": b"new second"})
    second = tmp_path / "second.csv"
    original_publish = transaction_module._publish_no_clobber

    def create_race_then_publish(source, destination):
        if Path(destination) == second:
            second.write_bytes(b"racing writer")
        return original_publish(source, destination)

    monkeypatch.setattr(transaction_module, "_publish_no_clobber", create_race_then_publish)

    try:
        with pytest.raises(ExportConflictError) as error:
            transaction.publish(staged)
    finally:
        transaction.cleanup()

    assert error.value.paths == (str(second.resolve()),)
    assert not (tmp_path / "first.csv").exists()
    assert second.read_bytes() == b"racing writer"


def test_rollback_failure_keeps_backup_and_reports_recovery_paths(tmp_path, monkeypatch):
    """情境：回滾失敗應保留備份並回報復原位置。"""
    import log_analyzer.infrastructure.exporting.transaction as transaction_module

    old_files = {
        tmp_path / "first.csv": b"old first",
        tmp_path / "second.csv": b"old second",
    }
    for path, content in old_files.items():
        path.write_bytes(content)
    transaction = ExportTransaction(tmp_path / "report.csv", overwrite=True)
    staged = _stage_files(transaction, {"first.csv": b"new first", "second.csv": b"new second"})
    original_publish = transaction_module._publish_no_clobber
    original_replace = transaction_module.os.replace

    def fail_second_publish(source, destination):
        if Path(destination).name == "second.csv":
            raise OSError("simulated publish failure")
        return original_publish(source, destination)

    def fail_first_restore(source, destination, *args, **kwargs):
        if "backups" in Path(source).parts and Path(destination).name == "first.csv":
            raise OSError("simulated restore failure")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(transaction_module, "_publish_no_clobber", fail_second_publish)
    monkeypatch.setattr(transaction_module.os, "replace", fail_first_restore)

    with pytest.raises(ExportRecoveryError) as error:
        transaction.publish(staged)

    recovery_dir = Path(error.value.recovery_dir)
    assert recovery_dir.is_dir()
    assert str((tmp_path / "first.csv").resolve()) in error.value.unrestored_paths
    assert str((tmp_path / "first.csv").resolve()) in error.value.published_paths
    assert any(recovery_dir.joinpath("backups").iterdir())
    assert str(recovery_dir) in str(error.value)
    assert (tmp_path / "first.csv").read_bytes() == b"new first"
    assert (tmp_path / "second.csv").read_bytes() == b"old second"
    transaction.cleanup()
    assert recovery_dir.exists(), "cleanup must preserve the only remaining backup"


def test_explicit_overwrite_repeated_success_cleans_transaction_directories(tmp_path):
    """情境：多次明確覆蓋成功後應清除交易暫存目錄。"""
    target = tmp_path / "report.csv"
    output = tmp_path / "first.csv"
    for value in (b"first", b"second"):
        transaction = ExportTransaction(target, overwrite=output.exists())
        staged = _stage_files(transaction, {"first.csv": value})
        try:
            transaction.publish(staged)
        finally:
            transaction.cleanup()
        assert output.read_bytes() == value

    assert not list(tmp_path.glob(".report.csv.export-*"))
