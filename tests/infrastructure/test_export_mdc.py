from __future__ import annotations

import csv
import json
from io import StringIO

from log_analyzer.infrastructure.exporting.csv import render_csv_report
from log_analyzer.infrastructure.exporting.json import render_json_report
from log_analyzer.infrastructure.exporting.markdown import render_markdown_report


def _log(mdc: dict[str, str] | None = None) -> dict:
    occurrence = {
        "filename": "a|b.log",
        "line_number": 4,
        "timestamp": "2026-06-06 10:00:00.001",
        "thread": "main",
    }
    if mdc:
        occurrence["mdc"] = mdc
    return {
        "count": 1,
        "line_numbers": [4],
        "timestamp": "2026-06-06 10:00:00.001",
        "last_timestamp": "2026-06-06 10:00:00.001",
        "level": "INFO",
        "thread": "main",
        "logger": "com.test.App",
        "filename": "a|b.log",
        "message": "message",
        "message_body": "",
        "stacktrace": "",
        "occurrences": [occurrence],
    }


def test_all_export_formats_preserve_occurrence_mdc():
    log = _log({"traceId": "trace-7", "user": "alice"})

    csv_rows = list(csv.reader(StringIO(render_csv_report({"INFO": 1}, [log]))))
    csv_detail = next(row for row in csv_rows if len(row) == 12 and row[8] == "message")
    markdown = render_markdown_report({"INFO": 1}, [log])
    json_result = json.loads(render_json_report({"INFO": 1}, [log]))["results"][0]

    assert csv_detail[-1] == (
        'a|b.log:4 | 2026-06-06 10:00:00.001 | main | MDC: '
        '{"traceId":"trace-7","user":"alice"}'
    )
    assert (
        'a|b.log:4 | 2026-06-06 10:00:00.001 | main | MDC: '
        '`{"traceId":"trace-7","user":"alice"}`'
    ) in markdown
    assert json_result["occurrences"][0]["mdc"] == {
        "traceId": "trace-7",
        "user": "alice",
    }


def test_selected_occurrences_field_controls_mdc_export():
    log = _log({"traceId": "trace-7"})

    selected_json = json.loads(
        render_json_report({"INFO": 1}, [log], fields=("occurrences",))
    )["results"][0]
    omitted_json = json.loads(
        render_json_report({"INFO": 1}, [log], fields=("message",))
    )["results"][0]
    selected_csv_rows = list(
        csv.reader(
            StringIO(render_csv_report({"INFO": 1}, [log], fields=("occurrences",)))
        )
    )
    selected_markdown = render_markdown_report(
        {"INFO": 1}, [log], fields=("occurrences",)
    )

    assert selected_json == {
        "occurrences": [
            {
                "filename": "a|b.log",
                "line_number": 4,
                "timestamp": "2026-06-06 10:00:00.001",
                "thread": "main",
                "mdc": {"traceId": "trace-7"},
            }
        ]
    }
    assert omitted_json == {"message": "message"}
    assert any(
        row == ['a|b.log:4 | 2026-06-06 10:00:00.001 | main | MDC: {"traceId":"trace-7"}']
        for row in selected_csv_rows
    )
    assert '`{"traceId":"trace-7"}`' in selected_markdown


def test_exports_without_mdc_keep_legacy_occurrence_text():
    log = _log()

    csv_rows = list(csv.reader(StringIO(render_csv_report({"INFO": 1}, [log]))))
    csv_detail = next(row for row in csv_rows if len(row) == 12 and row[8] == "message")
    markdown = render_markdown_report({"INFO": 1}, [log])
    json_result = json.loads(render_json_report({"INFO": 1}, [log]))["results"][0]

    assert csv_detail[-1] == "a|b.log:4 | 2026-06-06 10:00:00.001 | main"
    assert "a|b.log:4 | 2026-06-06 10:00:00.001 | main\n" in markdown
    assert "mdc" not in json_result["occurrences"][0]


def test_markdown_mdc_values_cannot_break_occurrence_lines():
    log = _log({"traceId": "line1\n### forged ` [link](url)"})

    markdown = render_markdown_report({"INFO": 1}, [log])

    assert "line1\\n### forged" in markdown
    assert "\n### forged" not in markdown
    assert "MDC: ``" in markdown
