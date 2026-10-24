import re


SOURCE_TEXT_ENCODING = "utf-8-sig"
SOURCE_TEXT_ERRORS = "surrogateescape"
SOURCE_TEXT_NEWLINE = None

_INVALID_UTF8_BYTE_PATTERN = re.compile(r"[\udc80-\udcff]")


def normalize_decoded_line(line: str) -> tuple[str, bool]:
    """Replace surrogateescaped invalid UTF-8 bytes and report their presence."""
    if _INVALID_UTF8_BYTE_PATTERN.search(line) is None:
        return line, False

    return _INVALID_UTF8_BYTE_PATTERN.sub("\ufffd", line), True
