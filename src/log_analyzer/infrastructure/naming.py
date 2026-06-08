from datetime import datetime


def build_timestamped_name(prefix: str) -> str:
    """回傳帶時間戳記的檔名前綴。"""
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
