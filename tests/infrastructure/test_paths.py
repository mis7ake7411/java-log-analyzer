from log_analyzer.infrastructure.paths import normalize_pasted_path, validate_pasted_path


def test_normalize_pasted_path_unquotes_file_url_and_decodes_characters(tmp_path):
    log_file = tmp_path / "app log.log"

    normalized = normalize_pasted_path(f'  "{log_file.as_uri()}"  ')

    assert normalized == str(log_file)


def test_normalize_pasted_path_rejects_multiline_text():
    assert normalize_pasted_path("/tmp/one\n/tmp/two") is None


def test_validate_pasted_log_file_uses_its_parent_directory(tmp_path):
    log_file = tmp_path / "application.out"
    log_file.touch()

    is_valid, normalized_path, _ = validate_pasted_path(str(log_file), "path")

    assert is_valid is True
    assert normalized_path == str(tmp_path)


def test_validate_pasted_log_path_rejects_unsupported_file_extension(tmp_path):
    config_file = tmp_path / "application.json"
    config_file.touch()

    is_valid, normalized_path, _ = validate_pasted_path(str(config_file), "path")

    assert is_valid is False
    assert normalized_path == str(config_file)


def test_validate_pasted_logback_path_accepts_only_xml(tmp_path):
    text_file = tmp_path / "logback.txt"
    text_file.touch()

    is_valid, normalized_path, _ = validate_pasted_path(str(text_file), "logback_xml_path")

    assert is_valid is False
    assert normalized_path == str(text_file)


def test_validate_pasted_output_path_requires_writable_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("log_analyzer.infrastructure.paths.os.access", lambda *_args: False)

    is_valid, normalized_path, _ = validate_pasted_path(str(tmp_path), "output_path")

    assert is_valid is False
    assert normalized_path == str(tmp_path)
