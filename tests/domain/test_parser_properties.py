from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import example, given, strategies as st

from log_analyzer.domain.parser import ParseOptions, parse_log_directory


@st.composite
def _single_line_messages(draw: st.DrawFn) -> str:
    characters = st.characters(categories=("L", "N", "P", "S", "Zs"))
    body = draw(st.text(alphabet=characters, min_size=0, max_size=60))
    return f"繁體訊息 {body} 結尾"


@example(message="前綴 - 訊息中保留 - 後綴")
@example(message="繁體中文與表情符號 🙂")
@given(message=_single_line_messages())
def test_parser_preserves_generated_single_line_unicode_messages(message: str) -> None:
    """以單行 Unicode 訊息確認解析不遺失正文或事件筆數。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        log_file = Path(temp_dir) / "application.log"
        log_file.write_text(
            "2026-06-06 10:00:00.001 [main] INFO  com.example.Service - "
            f"{message}\n",
            encoding="utf-8",
        )

        counts, logs = parse_log_directory(temp_dir, ParseOptions())
        try:
            assert counts["INFO"] == 1
            assert len(logs) == 1
            assert logs[0]["message"] == message
            assert logs[0]["count"] == 1
            assert len(logs[0]["occurrences"]) == 1
        finally:
            logs.release()
