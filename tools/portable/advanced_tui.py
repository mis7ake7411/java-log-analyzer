"""Exercise advanced settings through a real terminal and inspect exported evidence."""

import hashlib
import json
from pathlib import Path


PATTERN = "%d{yyyy-MM-dd HH:mm:ss,SSS} %-5level [%thread] %logger{0}: %msg%n"
MANUAL_PATTERN = PATTERN.replace("%msg", "%message")


def write_fixture(target):
    folder = target.data / "advanced-tui"
    folder.mkdir()
    (folder / "events.log").write_text(
        "2026-10-24 10:00:00,000 WARN  [worker-A] Service: Failure alpha\n"
        "2026-10-24 10:01:00,000 ERROR [worker-B] Service: Failure beta\n"
        "java.lang.IllegalStateException: portable-stack\n"
        "\tat com.example.Service.run(Service.java:42)\n"
        "2026-10-24 10:02:00,000 ERROR [worker-C] Service: Failure beta\n"
        "java.lang.IllegalStateException: portable-stack\n"
        "\tat com.example.Service.run(Service.java:42)\n"
        "2026-10-24 10:03:00,000 INFO  [main] Service: not-selected\n"
        "2026-10-24 11:00:00,000 ERROR [main] Service: outside-window\n",
        encoding="utf-8",
    )
    (folder / "format.xml").write_text(
        f'<configuration><property name="FILE_LOG_PATTERN" value="{PATTERN}"/>'
        '<property name="CONSOLE_LOG_PATTERN" value="[%d] [%level] %msg%n"/>'
        '</configuration>', encoding="utf-8",
    )
    lines = (folder / "events.log").read_text(encoding="utf-8").splitlines(keepends=True)
    (folder / "plain.log").write_text("".join(line for line in lines if line.startswith("2026-")), encoding="utf-8")
    return target.path("advanced-tui/events.log"), target.path("advanced-tui/format.xml")


def wait_analysis(terminal, count):
    terminal.wait(
        lambda: f"狀態：完成｜總Log{count}｜命中{count}" in "".join(terminal.text().split()),
        f"Advanced TUI analysis did not finish with {count} entries",
    )


def fill_visible_input(terminal, visible_text, value):
    terminal.fill_input(visible_text, value)


def select_option(terminal, current, selected, *, max_column=None, menu_ready=None):
    terminal.wait(lambda: terminal.find(current, max_column=max_column) is not None, f"Select not visible: {current}")
    column, row = terminal.find(current, max_column=max_column)
    terminal.click_at(column, row)
    terminal.wait_text(menu_ready or selected)
    terminal.click(selected)
    terminal.wait(
        lambda: any(selected in line[:max_column] and "▼" in line[:max_column]
                    for line in terminal.text().splitlines()),
        f"Select did not apply {selected}",
    )


def start_xml_analysis(terminal, source, xml):
    terminal.wait_text("開始分析")
    terminal.fill_source_input(source)
    terminal.wait_text(Path(source).name)
    terminal.click("展開進階設定")
    terminal.wait_text("Logback XML")
    terminal.click("例如：./logback-spring.xml")
    _, input_row = terminal.find("例如：./logback-spring.xml")
    terminal.wait(lambda: terminal.screen.cursor.y == input_row, "XML input did not receive focus")
    terminal.paste(xml)
    terminal.wait_text("format.xml")
    terminal.click("開始分析")


def verify_advanced_tui(target, root):
    source, xml = write_fixture(target)
    with target.terminal(root / "tui-xml-auto", dimensions=(80, 160)) as terminal:
        start_xml_analysis(terminal, target.path("advanced-tui/plain.log"), xml)
        wait_analysis(terminal, 5)
        terminal.wait_text("命中 5/5")
        terminal.capture("xml-analysis")
        terminal.send("q")
        terminal.wait_exit()
    with target.terminal(root / "tui-advanced", dimensions=(80, 160)) as terminal:
        start_xml_analysis(terminal, source, xml)
        terminal.wait_text("確認 Logback Pattern")
        terminal.wait_text("命中 5/9")
        terminal.capture("xml-confidence-confirmation")
        terminal.click("使用此 Pattern（命中 5/9）")
        wait_analysis(terminal, 5)
        apply_manual_filters(terminal)
        browse_source(terminal)
        export_selected_fields(terminal)
        terminal.send("q")
        terminal.wait_exit()
    verify_export(target)
    return ["tui-xml-auto-pattern", "tui-xml-confidence-confirmation", "tui-manual-pattern-filters-sort", "tui-event-source-stacktrace",
            "tui-selected-fields-json-manifest"]


def apply_manual_filters(terminal):
    terminal.click("手動覆寫 Pattern")
    terminal.wait_text("僅支援常見 Logback token")
    terminal.fill_input("yyyy-MM-dd", MANUAL_PATTERN, max_column=80)
    terminal.capture("manual-pattern")
    fill_visible_input(terminal, "Order_123", "failure")
    fill_visible_input(terminal, "YYYY-MM-DD", "2026-10-24")
    fill_visible_input(terminal, "HH:MM", "10:00:00")
    fill_visible_input(terminal, "YYYY-MM-DD", "2026-10-24")
    fill_visible_input(terminal, "HH:MM", "10:02:00")
    select_option(terminal, "時間排序", "Level 分組", max_column=80)
    terminal.click("開始分析")
    wait_analysis(terminal, 3)
    terminal.capture("filtered-analysis")


def browse_source(terminal):
    terminal.click("查看事件原文")
    terminal.wait_text("符合事件群組")
    terminal.wait_text("Failure beta")
    terminal.send("\r")
    terminal.wait_text("出現位置")
    terminal.send("\r")
    terminal.wait_text("portable-stack")
    terminal.wait_text("Service.java:42")
    terminal.wait_text("複製路徑:行號")
    terminal.capture("event-source-preview")
    terminal.click("關閉")
    terminal.wait(
        lambda: terminal.find("來源內容預覽") is None
        and terminal.find("事件出現位置") is None
        and terminal.find("匯出結果") is not None,
        "Event browser did not close",
    )


def export_selected_fields(terminal):
    terminal.click("匯出結果")
    terminal.wait_text("輸出檔名")
    terminal.wait_text("執行匯出")
    terminal.wait_focused_button("執行匯出")
    terminal.capture("export-settings")
    # From confirm: manifest, twelve fields, level-split, then format.
    # Keyboard traversal keeps focus through the form's automatic scrolling.
    terminal.send("\x1b[Z" * 15 + "\r")
    terminal.wait_text("Markdown")
    terminal.send("\x1b[B\r")
    terminal.wait(lambda: terminal.find("CSV (Excel)", max_column=80) is None, "JSON was not selected")
    # Format -> split -> count; then eleven other fields -> manifest -> confirm.
    terminal.send("\t\t " + "\t" * 12 + " " + "\t\r")
    terminal.wait_text("證據清單：")
    terminal.capture("advanced-export-complete")


def verify_export(target):
    output_dir = target.data / "work A"
    manifests = list(output_dir.glob("*_manifest.json"))
    assert len(manifests) == 1, manifests
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    filters = manifest["analysis"]["filters"]
    assert filters["pattern"] == MANUAL_PATTERN
    assert filters["keyword"] == "failure" and filters["ignore_case"] is True
    assert filters["sort_by"] == "level"
    assert filters["start_dt"].startswith("2026-10-24T10:00:00")
    assert filters["end_dt"].startswith("2026-10-24T10:02:00")
    assert manifest["analysis"]["matched_occurrences"] == 3
    assert "count" not in manifest["export"]["fields"]
    assert manifest["export"]["format"] == "json"
    assert len(manifest["outputs"]) == 1
    report = output_dir / manifest["outputs"][0]["filename"]
    assert hashlib.sha256(report.read_bytes()).hexdigest() == manifest["outputs"][0]["sha256"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"] == {"ERROR": 2, "WARN": 1}
    groups = payload["results"]
    assert [group["level"] for group in groups] == ["ERROR", "ERROR", "WARN"]
    assert all("count" not in group for group in groups)
    assert [coordinate["line_number"] for group in groups[:2] for coordinate in group["occurrences"]] == [2, 5]
    assert all("portable-stack" in group["stacktrace"] for group in groups[:2])
    assert all("Service.java:42" in group["stacktrace"] for group in groups[:2])
