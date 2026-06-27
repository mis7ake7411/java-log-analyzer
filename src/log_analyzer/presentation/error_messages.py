from __future__ import annotations

import re


def get_error_hint(title: str, message: str = "") -> str:
    text = f"{title} {message}".strip()

    if "開始日期時間格式不正確" in text or "結束日期時間格式不正確" in text:
        return "請改用 YYYY-MM-DD、YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS；若只填時間，請先補日期。"

    if "請先輸入開始日期" in text or "請先輸入結束日期" in text:
        return "日期與時間需成對輸入；只填時間時，請先補上日期。"

    if "開始時間不能晚於結束時間" in text:
        return "請確認起訖順序，讓開始時間早於或等於結束時間。"

    if "不支援的 Logback pattern token" in text:
        token_match = re.search(r"%[A-Za-z]+", text)
        token = token_match.group(0) if token_match else "不支援的 token"
        return f"請移除不支援的 Logback pattern token（{token}），或改用常見的 %d、%thread、%level、%logger、%msg。"

    if "找不到可用的 Logback pattern" in text or "logback XML 中找不到可用的 pattern" in text:
        return "請改選其他 logback.xml，或手動切到進階 Pattern。"

    if "權限不足" in text:
        return "請確認路徑權限，或改用可讀 / 可寫的目錄。"

    if "找不到資料夾" in text or "找不到目錄" in text:
        return "請檢查路徑是否存在，或重新選擇 Log 目錄。"

    if "找不到符合條件的 log" in text:
        return "請確認 Log 目錄、時間區間、關鍵字與 Pattern 是否符合實際資料。"

    if title == "輸入錯誤":
        return "請先檢查日期 / 時間格式、路徑與 Pattern 是否正確。"

    if title == "執行失敗":
        return "若問題持續發生，請先縮小條件範圍再重試。"

    return "請檢查輸入條件後再試一次。"
