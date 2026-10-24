from log_analyzer.infrastructure.naming import build_timestamped_name


def test_build_timestamped_name_uses_prefix():
    """情境：時間戳檔名應保留指定前綴。"""
    name = build_timestamped_name("log_analysis")

    assert name.startswith("log_analysis_")
