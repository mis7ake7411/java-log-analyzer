from __future__ import annotations
from textual.widgets import Static


class ResultContextNotice(Static):
    DEFAULT_CSS = """
    ResultContextNotice { display: none; height: auto; color: #fbbf24; }
    #export-result-context { height: auto; color: #7dd3fc; margin-bottom: 1; }
    """
