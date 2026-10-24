from io import BytesIO, TextIOWrapper

import pytest


def _normalize_stream(data: bytes) -> list[tuple[str, bool]]:
    from log_analyzer.domain.text_decoding import (
        SOURCE_TEXT_ENCODING,
        SOURCE_TEXT_ERRORS,
        SOURCE_TEXT_NEWLINE,
        normalize_decoded_line,
    )

    with TextIOWrapper(
        BytesIO(data),
        encoding=SOURCE_TEXT_ENCODING,
        errors=SOURCE_TEXT_ERRORS,
        newline=SOURCE_TEXT_NEWLINE,
    ) as stream:
        return [normalize_decoded_line(line) for line in stream]


def test_normalize_decoded_line_keeps_regular_text_and_whitespace_unchanged():
    from log_analyzer.domain.text_decoding import normalize_decoded_line

    line = "  中文 message\t\r\n"

    assert normalize_decoded_line(line) == (line, False)


def test_utf8_sig_stream_strips_bom_and_preserves_valid_replacement_character():
    data = b"\xef\xbb\xbf\xe4\xb8\xad \xef\xbf\xbd\r\n"

    assert _normalize_stream(data) == [("中 �\n", False)]


@pytest.mark.parametrize(
    ("invalid_bytes", "expected"),
    [
        (b"\xff", "\ufffd"),
        (b"\xe2(\xa1", "\ufffd(\ufffd"),
        (b"\xe2\x82", "\ufffd\ufffd"),
    ],
    ids=["single-byte", "malformed-multibyte", "truncated-multibyte"],
)
def test_invalid_utf8_bytes_are_replaced_and_reported(invalid_bytes, expected):
    assert _normalize_stream(b"before" + invalid_bytes + b"after\n") == [
        ("before" + expected + "after\n", True)
    ]


def test_invalid_utf8_sequence_split_by_newline_is_reported_on_each_physical_line():
    data = b"first\xe4\r\n\xb8\xad\rhealthy\n"

    assert _normalize_stream(data) == [
        ("first\ufffd\n", True),
        ("\ufffd\ufffd\n", True),
        ("healthy\n", False),
    ]


def test_normalize_decoded_line_does_not_modify_other_surrogate_codepoints():
    from log_analyzer.domain.text_decoding import normalize_decoded_line

    line = "\udc7f\udc80\udd00\udcff\r\n"

    assert normalize_decoded_line(line) == (
        "\udc7f\ufffd\udd00\ufffd\r\n",
        True,
    )
