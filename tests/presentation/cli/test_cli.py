import logging
from types import SimpleNamespace

import pytest

from log_analyzer.application.analysis_service import NoMatchingLogsError
from log_analyzer.domain.log_types import ScanDiagnostics
from log_analyzer.presentation.cli import entrypoint as cli


def _analysis_result():
    return SimpleNamespace(
        output_path="/tmp/report.csv",
        exported_files=["/tmp/report.csv"],
        counts={"ERROR": 2, "INFO": 0},
    )


def test_cli_success_warns_when_invalid_utf8_was_replaced(capsys):
    """情境：CLI 成功分析遇解碼替代時應附警告。"""
    result = _analysis_result()
    result.diagnostics = ScanDiagnostics(2, 4, 4, 4, 4, 1, 3)
    args = SimpleNamespace(format="csv", max_export_mb=None, split_by_level=False)

    cli._print_analysis_result(result, args)

    output = capsys.readouterr().out
    assert "UTF-8" in output
    assert "�" in output
    assert "3" in output


def test_cli_zero_result_warns_when_invalid_utf8_was_replaced(capsys):
    """情境：CLI 零結果遇解碼替代時也應附警告。"""
    with pytest.raises(SystemExit) as exc_info:
        cli._handle_cli_error(NoMatchingLogsError(ScanDiagnostics(1, 0, 0, 0, 0, 1, 2)))

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "UTF-8" in output
    assert "�" in output
    assert "2" in output


def test_main_logs_unexpected_error_and_keeps_friendly_message(
    monkeypatch, caplog, capsys, tmp_path
):
    """情境：main 應記錄非預期錯誤並保留友善訊息。"""
    source_dir = tmp_path
    (source_dir / "sample.log").write_text("sample\n", encoding="utf-8")

    def raise_unexpected(_options):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", str(source_dir)])
    monkeypatch.setattr(cli, "resolve_target_dir", lambda _directory: (str(source_dir), None))
    monkeypatch.setattr(cli, "resolve_logback_pattern", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(cli, "run_analysis_with_options", raise_unexpected)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "來源檔數：1" in output
    assert "錯誤：unexpected failure" in output
    assert "unexpected failure" in caplog.text


def test_main_passes_analysis_options_and_prints_success_result(
    monkeypatch, capsys, tmp_path
):
    """情境：main 應傳入 AnalysisOptions 並顯示成功摘要。"""
    source_dir = tmp_path
    for filename in ("alpha.LOG", "middle.txt", "omega.OUT"):
        (source_dir / filename).write_text("sample\n", encoding="utf-8")
    (source_dir / "ignored.json").write_text("ignored\n", encoding="utf-8")
    (source_dir / "nested").mkdir()
    (source_dir / "nested" / "inner.log").write_text("ignored\n", encoding="utf-8")
    (source_dir / "folder.log").mkdir()

    captured_options = None
    scope_output_before_analysis = None
    resolved_scopes = []
    target_dir = str(source_dir)

    def run_with_options(options):
        nonlocal captured_options, scope_output_before_analysis
        captured_options = options
        scope_output_before_analysis = capsys.readouterr().out
        return _analysis_result()

    def resolve_pattern(logback_xml, resolved_dir, selected_pattern, *, selected_files=None):
        resolved_scopes.append((logback_xml, resolved_dir, selected_pattern, selected_files))
        return "%msg%n", None

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["log-analyzer", "logs", "--keyword", "Order", "--ignore-case"],
    )
    monkeypatch.setattr(cli, "resolve_target_dir", lambda _directory: (target_dir, None))
    monkeypatch.setattr(cli, "resolve_logback_pattern", resolve_pattern)
    monkeypatch.setattr(cli, "run_analysis_with_options", run_with_options)

    cli.main()

    assert captured_options is not None
    assert captured_options.path == target_dir
    assert captured_options.selected_files == ("alpha.LOG", "middle.txt", "omega.OUT")
    assert captured_options.output_path.endswith(".csv")
    assert captured_options.keyword == "Order"
    assert captured_options.ignore_case is True
    assert captured_options.log_pattern == "%msg%n"
    assert captured_options.include_details is False
    assert captured_options.levels == ("ERROR", "WARN", "INFO")
    assert resolved_scopes == [(None, target_dir, None, ("alpha.LOG", "middle.txt", "omega.OUT"))]

    output = scope_output_before_analysis + capsys.readouterr().out
    assert output.splitlines() == [
        "來源模式：資料夾（不含子目錄）",
        f"來源路徑：{target_dir}",
        "來源檔數：3",
        "完整檔名清單：",
        "  - alpha.LOG",
        "  - middle.txt",
        "  - omega.OUT",
        "分析 Level：ERROR, WARN, INFO",
        "正在搜尋關鍵字：'Order' (忽略大小寫)",
        "分析完成！報表已儲存至：/tmp/report.csv (格式: CSV)",
        "",
        "符合條件的統計摘要 (Summary)：",
        "  ERROR: 2",
    ]


def test_main_freezes_single_file_scope_for_xml_and_analysis(
    monkeypatch, capsys, tmp_path
):
    """情境：XML 抽樣與實際分析應共用固定單檔範圍。"""
    selected_file = tmp_path / "chosen.LoG"
    sibling_file = tmp_path / "sibling.txt"
    xml_file = tmp_path / "logback.xml"
    selected_file.write_text("selected marker\n", encoding="utf-8")
    sibling_file.write_text("sibling marker\n", encoding="utf-8")
    xml_file.write_text("<configuration />", encoding="utf-8")

    captured_options = None
    scope_output_before_analysis = None
    resolved_scopes = []

    def run_with_options(options):
        nonlocal captured_options, scope_output_before_analysis
        captured_options = options
        scope_output_before_analysis = capsys.readouterr().out
        return _analysis_result()

    def resolve_pattern(logback_xml, resolved_path, selected_pattern, *, selected_files=None):
        resolved_scopes.append((logback_xml, resolved_path, selected_pattern, selected_files))
        return "%msg%n", None

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["log-analyzer", str(selected_file), "--logback-xml", str(xml_file)],
    )
    monkeypatch.setattr(cli, "resolve_logback_pattern", resolve_pattern)
    monkeypatch.setattr(cli, "run_analysis_with_options", run_with_options)

    cli.main()

    selected_path = str(selected_file.resolve())
    assert captured_options is not None
    assert captured_options.path == selected_path
    assert captured_options.selected_files == ("chosen.LoG",)
    assert resolved_scopes == [(str(xml_file), selected_path, None, ("chosen.LoG",))]
    assert "sibling.txt" not in scope_output_before_analysis
    assert scope_output_before_analysis.splitlines() == [
        "來源模式：單一檔案",
        f"來源路徑：{selected_path}",
        "來源檔數：1",
        "完整檔名清單：",
        "  - chosen.LoG",
        "分析 Level：ERROR, WARN, INFO",
    ]


def test_uncertain_xml_pattern_exits_before_creating_report(monkeypatch, capsys, tmp_path):
    """情境：XML 候選不確定時應在建立報表前退出。"""
    source_dir = tmp_path / "logs"
    source_dir.mkdir()
    (source_dir / "app.log").write_text("not a log line\n", encoding="utf-8")
    xml_path = tmp_path / "logback.xml"
    xml_path.write_text(
        '<configuration><property name="LOG_PATTERN" value="%d [%thread] %-5level %logger - %msg%n"/></configuration>',
        encoding="utf-8",
    )
    output_path = tmp_path / "report.csv"
    analysis_calls = []

    def capture_analysis(options):
        analysis_calls.append(options)
        return _analysis_result()

    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "log-analyzer",
            str(source_dir),
            "--logback-xml",
            str(xml_path),
            "-o",
            str(output_path),
        ],
    )
    monkeypatch.setattr(cli, "run_analysis_with_options", capture_analysis)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert not analysis_calls
    assert not output_path.exists()
    output = capsys.readouterr().out
    assert "--pattern" in output
    assert "不確定" in output


def test_unsupported_xml_pattern_reports_candidate_error_before_analysis(monkeypatch, capsys, tmp_path):
    """情境：全部 XML 候選不支援時應在分析前顯示候選錯誤。"""
    source_dir = tmp_path / "logs"
    source_dir.mkdir()
    (source_dir / "app.log").write_text("sample\n", encoding="utf-8")
    xml_path = tmp_path / "logback.xml"
    xml_path.write_text(
        '<configuration><property name="FILE_LOG_PATTERN" value="%d %springProperty%n"/></configuration>',
        encoding="utf-8",
    )
    output_path = tmp_path / "report.csv"
    analysis_calls = []

    def capture_analysis(options):
        analysis_calls.append(options)
        return _analysis_result()

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["log-analyzer", str(source_dir), "--logback-xml", str(xml_path), "-o", str(output_path)],
    )
    monkeypatch.setattr(cli, "run_analysis_with_options", capture_analysis)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert not analysis_calls
    assert not output_path.exists()
    output = capsys.readouterr().out
    assert "FILE_LOG_PATTERN" in output
    assert "%springProperty" in output


def test_main_rejects_empty_directory_without_expanding_to_parent(
    monkeypatch, capsys, tmp_path
):
    """情境：空資料夾不得擴大到父目錄分析。"""
    parent_dir = tmp_path
    empty_dir = parent_dir / "empty"
    empty_dir.mkdir()
    (parent_dir / "parent.log").write_text("must not be selected\n", encoding="utf-8")
    service_called = False

    def run_with_options(_options):
        nonlocal service_called
        service_called = True
        return _analysis_result()

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", str(empty_dir)])
    monkeypatch.setattr(cli, "run_analysis_with_options", run_with_options)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    output = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert service_called is False
    assert "來源檔數：0" in output
    assert "完整檔名清單：" in output
    assert "找不到支援的 Log 檔案" in output
    assert "parent.log" not in output


def test_main_rejects_missing_file_without_scanning_parent(
    monkeypatch, capsys, tmp_path
):
    """情境：指定檔案遺失不得退回掃描父目錄。"""
    parent_dir = tmp_path
    missing_file = parent_dir / "missing.log"
    (parent_dir / "sibling.log").write_text("must not be selected\n", encoding="utf-8")
    service_called = False

    def run_with_options(_options):
        nonlocal service_called
        service_called = True
        return _analysis_result()

    monkeypatch.setattr(cli.sys, "argv", ["log-analyzer", str(missing_file)])
    monkeypatch.setattr(cli, "run_analysis_with_options", run_with_options)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    output = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert service_called is False
    assert "找不到日誌來源" in output
    assert "sibling.log" not in output
