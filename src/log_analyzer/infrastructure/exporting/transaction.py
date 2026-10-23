from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from collections.abc import Sequence


class ExportConflictError(FileExistsError):
    """匯出目標已有檔案，且呼叫端未明確允許覆蓋。"""

    def __init__(self, paths: Sequence[str | os.PathLike[str]]) -> None:
        self.paths = tuple(os.path.abspath(os.fspath(path)) for path in paths)
        details = "\n".join(f"- {path}" for path in self.paths)
        super().__init__(f"匯出目標已存在，未覆蓋任何檔案：\n{details}")


class UnsafeExportTargetError(OSError):
    """匯出目標不是可安全替換的一般檔案。"""


class ExportRecoveryError(OSError):
    """匯出失敗後無法完整還原，並保留可復原資料。"""

    def __init__(
        self,
        cause: BaseException,
        published_paths: Sequence[str],
        unrestored_paths: Sequence[str],
        recovery_dir: str,
    ) -> None:
        self.cause = cause
        self.published_paths = tuple(published_paths)
        self.unrestored_paths = tuple(unrestored_paths)
        self.recovery_dir = recovery_dir
        published = "、".join(self.published_paths) or "無"
        unrestored = "、".join(self.unrestored_paths) or "無"
        super().__init__(
            "匯出失敗後無法完整復原；仍發布的目標："
            f"{published}；未復原目標：{unrestored}；"
            f"備份與暫存資料保留於：{recovery_dir}"
        )


class ExportTransaction:
    """在目的目錄同一檔案系統 staging，並安全發布一組輸出檔案。"""

    def __init__(
        self,
        output_path: str | os.PathLike[str],
        *,
        overwrite: bool = False,
        protected_paths: Sequence[str | os.PathLike[str]] = (),
    ) -> None:
        self.output_path = Path(output_path)
        self.destination_dir = self.output_path.parent
        self.overwrite = overwrite
        self.protected_paths = tuple(Path(path) for path in protected_paths)
        self.stage_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{self.output_path.name}.export-",
                dir=self.destination_dir,
            )
        )
        self._preserve_stage = False
        self._published_paths: tuple[str, ...] = ()

    def stage_path(self, name: str) -> Path:
        return self.stage_dir / name

    def target_for(self, staged_path: str | os.PathLike[str]) -> Path:
        return self.destination_dir / Path(staged_path).name

    def publish(self, staged_paths: Sequence[str | os.PathLike[str]]) -> list[str]:
        staged = [Path(path) for path in staged_paths]
        targets = [self.target_for(path) for path in staged]
        self._validate_staged_files(staged)
        self._validate_distinct_targets(targets)
        self._validate_protected_targets(targets)

        existing_at_start = {target: _validate_target(target) for target in targets}
        conflicts = [target for target, exists in existing_at_start.items() if exists]
        if conflicts and not self.overwrite:
            raise ExportConflictError(conflicts)
        original_identities = {
            target: _file_identity(target)
            for target, exists in existing_at_start.items()
            if exists
        }

        backup_dir = self.stage_dir / "backups"
        backups: dict[Path, Path] = {}
        published: dict[Path, tuple[int, int]] = {}
        try:
            if self.overwrite:
                # Once an original can be moved, cleanup must not remove the
                # only backup if another interrupt arrives during rollback.
                self._preserve_stage = True
                for index, target in enumerate(targets):
                    exists_now = _validate_target(target)
                    if exists_now and not existing_at_start[target]:
                        raise ExportConflictError([target])
                    if not exists_now:
                        continue
                    backup_dir.mkdir(exist_ok=True)
                    backup = backup_dir / f"{index:04d}-{target.name}"
                    # Record the intended backup name before moving the file.
                    # If an interrupt arrives just after os.replace succeeds,
                    # rollback can still discover and restore the backup.
                    backups[target] = backup
                    os.replace(target, backup)

            for source, target in zip(staged, targets, strict=True):
                _validate_target(target)
                identity = _file_identity(source)
                # Register the staged file identity before publication. This
                # lets rollback recognize our target if an interrupt is raised
                # immediately after the OS operation succeeds.
                published[target] = identity
                try:
                    _publish_no_clobber(source, target)
                except FileExistsError as error:
                    raise ExportConflictError([target]) from error

            self._published_paths = tuple(
                str(target.resolve(strict=False)) for target in targets
            )
            self._preserve_stage = False
            return [str(target) for target in targets]
        except BaseException as error:
            self._preserve_stage = True
            try:
                unrestored = self._rollback(targets, backups, published, original_identities)
            except BaseException as rollback_error:
                remaining_published, unrestored = self._recovery_state(
                    targets, backups, published, original_identities
                )
                raise ExportRecoveryError(
                    rollback_error,
                    remaining_published,
                    [str(path.resolve(strict=False)) for path in unrestored],
                    str(self.stage_dir.resolve(strict=False)),
                ) from rollback_error
            if unrestored:
                remaining_published, _ = self._recovery_state(
                    targets, backups, published, original_identities
                )
                raise ExportRecoveryError(
                    error,
                    remaining_published,
                    [str(path.resolve(strict=False)) for path in unrestored],
                    str(self.stage_dir.resolve(strict=False)),
                ) from error
            self._preserve_stage = False
            raise

    def cleanup(self) -> None:
        if not self._preserve_stage:
            try:
                shutil.rmtree(self.stage_dir)
            except OSError as error:
                if not self.stage_dir.exists():
                    return
                self._preserve_stage = True
                stage_path = str(self.stage_dir.resolve(strict=False))
                if self._published_paths:
                    published = ", ".join(self._published_paths)
                    status = f"輸出已發布至：{published}；"
                else:
                    status = "尚無輸出發布；"
                raise OSError(
                    f"{status}暫存清理失敗，請檢查或移除暫存目錄：{stage_path}"
                ) from error

    def _validate_staged_files(self, staged: Sequence[Path]) -> None:
        stage_root = self.stage_dir.resolve()
        for path in staged:
            try:
                resolved_parent = path.parent.resolve()
            except (OSError, RuntimeError) as error:
                raise UnsafeExportTargetError(f"無法確認暫存檔路徑：{path}") from error
            if resolved_parent != stage_root:
                raise UnsafeExportTargetError(f"暫存檔不在交易目錄內：{path}")
            _require_regular_file(path, "暫存")

    def _validate_distinct_targets(self, targets: Sequence[Path]) -> None:
        normalized = [os.path.normcase(str(target.resolve(strict=False))) for target in targets]
        if len(normalized) != len(set(normalized)):
            raise UnsafeExportTargetError("匯出目標名稱重複，無法安全發布")

    def _validate_protected_targets(self, targets: Sequence[Path]) -> None:
        protected = {_canonical(path) for path in self.protected_paths}
        collisions = [target for target in targets if _canonical(target) in protected]
        if collisions:
            details = "、".join(str(path.resolve(strict=False)) for path in collisions)
            raise ValueError(f"匯出目標與輸入來源重疊，已拒絕寫入來源檔案：{details}")

    def _rollback(
        self,
        targets: Sequence[Path],
        backups: dict[Path, Path],
        published: dict[Path, tuple[int, int]],
        original_identities: dict[Path, tuple[int, int]],
    ) -> list[Path]:
        unrestored: list[Path] = []
        for target in reversed(targets):
            backup = backups.get(target)
            identity = published.get(target)
            backup_exists = backup is not None and _path_exists(backup)
            try:
                exists_now = _validate_target(target)
            except OSError:
                unrestored.append(target)
                continue

            if backup is not None:
                if not backup_exists:
                    # The backup move did not complete if the original inode
                    # is still at the target. Otherwise the old copy is lost.
                    backups.pop(target, None)
                    original_identity = original_identities.get(target)
                    if not (
                        exists_now
                        and original_identity is not None
                        and _same_identity(target, original_identity)
                    ):
                        unrestored.append(target)
                    continue
                if exists_now and (identity is None or not _same_identity(target, identity)):
                    unrestored.append(target)
                    continue
                try:
                    os.replace(backup, target)
                    backups.pop(target, None)
                    published.pop(target, None)
                except OSError:
                    unrestored.append(target)
                continue

            if identity is None or not exists_now:
                continue
            if not _same_identity(target, identity):
                # This target was absent at preflight and a different file
                # appeared during publication. Never remove an external file.
                continue
            try:
                target.unlink()
                published.pop(target, None)
            except OSError:
                unrestored.append(target)

        # Any backups still present are the only remaining copy of the old
        # target. Keep the transaction directory if one cannot be restored.
        for target, backup in backups.items():
            if target not in unrestored:
                unrestored.append(target)
        return unrestored

    def _recovery_state(
        self,
        targets: Sequence[Path],
        backups: dict[Path, Path],
        published: dict[Path, tuple[int, int]],
        original_identities: dict[Path, tuple[int, int]],
    ) -> tuple[list[str], list[Path]]:
        remaining_published: list[str] = []
        unrestored: list[Path] = []
        for target in targets:
            identity = published.get(target)
            try:
                exists_now = _validate_target(target)
                matches_published = (
                    identity is not None and exists_now and _same_identity(target, identity)
                )
            except BaseException:  # noqa: BLE001 - still report the best-effort recovery state
                matches_published = False
                exists_now = True
            if matches_published:
                remaining_published.append(str(target.resolve(strict=False)))

            backup = backups.get(target)
            try:
                backup_exists = backup is not None and _path_exists(backup)
            except BaseException:  # noqa: BLE001 - preserve the stage if recovery-path probing fails
                backup_exists = True
            if backup is not None:
                original_identity = original_identities.get(target)
                original_remains = (
                    exists_now
                    and original_identity is not None
                    and _same_identity(target, original_identity)
                )
                if backup_exists or not original_remains:
                    unrestored.append(target)
            elif identity is not None and exists_now and matches_published:
                unrestored.append(target)
        return remaining_published, unrestored


def _validate_target(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return False
    _require_regular_stat(file_stat, path)
    return True


def _require_regular_file(path: Path, description: str) -> None:
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise UnsafeExportTargetError(f"無法檢查{description}檔案：{path}") from error
    _require_regular_stat(file_stat, path)


def _require_regular_stat(file_stat: os.stat_result, path: Path) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(file_stat, "st_file_attributes", 0)
    if stat.S_ISLNK(file_stat.st_mode) or (reparse_flag and attributes & reparse_flag):
        raise UnsafeExportTargetError(f"拒絕符號連結或 reparse point 匯出目標：{path}")
    if not stat.S_ISREG(file_stat.st_mode):
        raise UnsafeExportTargetError(f"匯出目標必須是一般檔案：{path}")


def _file_identity(path: Path) -> tuple[int, int]:
    file_stat = path.stat()
    return file_stat.st_dev, file_stat.st_ino


def _same_identity(path: Path, identity: tuple[int, int]) -> bool:
    try:
        return _file_identity(path) == identity
    except OSError:
        return False


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _canonical(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _publish_no_clobber(source: Path, target: Path) -> None:
    if os.name == "nt":
        # Windows rename fails atomically when the destination already exists.
        # This avoids hard-link privileges that may be unavailable to users.
        os.rename(source, target)
        return
    # POSIX rename may replace an existing destination, so link creation gives
    # the atomic EEXIST behavior needed for default no-clobber publication.
    os.link(source, target)
