"""即時追蹤專用分析條件。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from log_analyzer.application.analysis.service import AnalysisRequest


@dataclass(frozen=True, slots=True)
class LiveAnalysisRequest(AnalysisRequest):
    """在既有分析條件上加上固定的即時相似訊息條件。

    Args:
        similar_to: 參考訊息；None 表示停用相似篩選。
        similarity_threshold: 文字相似分數門檻，範圍為 0 到 100。

    Raises:
        TypeError: 參考訊息不是字串。
        ValueError: 參考訊息空白，或門檻不是有限的 0 到 100 數值。
    """

    similar_to: str | None = None
    similarity_threshold: float = 80.0

    def __post_init__(self) -> None:
        if self.similar_to is not None:
            if not isinstance(self.similar_to, str):
                raise TypeError("similar_to must be a string or None")
            if not self.similar_to.strip():
                raise ValueError("similar_to cannot be empty")
        threshold = self.similarity_threshold
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0 <= threshold <= 100
            or not isfinite(threshold)
        ):
            raise ValueError(
                "similarity_threshold must be a finite number from 0 to 100"
            )
