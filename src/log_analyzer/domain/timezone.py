from __future__ import annotations

from datetime import datetime


def to_local_timezone(value: datetime) -> datetime:
    """將時間統一正規化為系統本地時區。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.astimezone()
