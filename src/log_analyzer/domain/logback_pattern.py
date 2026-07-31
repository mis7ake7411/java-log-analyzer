import re
from re import Pattern


DEFAULT_LOGBACK_PATTERN = "%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n"
TIMESTAMP_PATTERN = r"(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[\.,]\d{3})?)"
DEFAULT_LOGBACK_REGEX = re.compile(
    rf'^{TIMESTAMP_PATTERN}\s+'
    r'\[(?P<thread>.*?)\]\s+(?P<level>\w+)\s+(?P<logger>.*?)\s+-\s+(?P<message>.*)$'
)

SUPPORTED_TOKENS = {
    "d",
    "date",
    "thread",
    "t",
    "level",
    "le",
    "p",
    "logger",
    "lo",
    "c",
    "C",
    "class",
    "msg",
    "message",
    "m",
    "n",
    "ex",
    "throwable",
    "xEx",
    "wEx",
    "wex",
    "rootException",
    "rEx",
    "file",
    "line",
    "L",
    "class",
    "method",
    "M",
    "caller",
    "marker",
    "mdc",
    "X",
    "pid",
    "property",
}

FIELD_PATTERNS = {
    "d": TIMESTAMP_PATTERN,
    "date": TIMESTAMP_PATTERN,
    "thread": r"(?P<thread>.*?)",
    "t": r"(?P<thread>.*?)",
    "level": r"\s*(?P<level>\w+)\s*",
    "le": r"\s*(?P<level>\w+)\s*",
    "p": r"\s*(?P<level>\w+)\s*",
    "logger": r"(?P<logger>.+?\S)\s*",
    "lo": r"(?P<logger>.+?\S)\s*",
    "c": r"(?P<logger>.+?\S)\s*",
    "C": r"(?P<logger>.+?\S)\s*",
    "class": r"(?P<logger>.+?\S)\s*",
    "msg": r"(?P<message>.*)",
    "message": r"(?P<message>.*)",
    "m": r"(?P<message>.*)",
}

PASS_THROUGH_PATTERNS = {
    "file": r"\S+",
    "line": r"\d+",
    "L": r"\d+",
    "class": r"\S+",
    "method": r"\S+",
    "M": r"\S+",
    "caller": r".*?",
    "marker": r".*?",
    "mdc": r".*?",
    "X": r".*?",
    "pid": r"\S+",
    "property": r".*?",
    "ex": r".*",
    "throwable": r".*",
    "xEx": r".*",
    "wEx": r".*",
    "wex": r".*",
    "rootException": r".*",
    "rEx": r".*",
}

_PATTERN_TOKEN = re.compile(r"%(-?\d*(?:\.\d+)?)?([A-Za-z]+)(?:\{[^}]*\})?")


class UnsupportedLogbackPatternError(ValueError):
    """Raised when a Logback pattern contains unsupported conversion words."""


def compile_logback_pattern(pattern: str) -> Pattern[str]:
    """Compile a supported subset of Logback PatternLayout syntax into a regex."""
    cleaned = _expand_logback_default_placeholders(pattern.strip())
    cleaned = _strip_wrapper_converters(cleaned)
    if not cleaned:
        return DEFAULT_LOGBACK_REGEX

    regex = _build_pattern_regex(cleaned)
    _ensure_required_groups(regex)
    return re.compile(regex)


def _build_pattern_regex(pattern: str) -> str:
    regex_parts = ["^"]
    pos = 0
    for match in _PATTERN_TOKEN.finditer(pattern):
        literal = pattern[pos:match.start()]
        regex_parts.append(_literal_to_regex(literal))
        regex_parts.append(_token_to_regex(pattern, match))
        pos = match.end()

    regex_parts.append(_literal_to_regex(pattern[pos:]))
    regex_parts.append("$")
    return "".join(regex_parts)


def _token_to_regex(pattern: str, match: re.Match[str]) -> str:
    token = match.group(2)
    if token not in SUPPORTED_TOKENS:
        raise UnsupportedLogbackPatternError(f"不支援的 Logback pattern token：%{token}")

    if token == "n":
        return r"\s*"
    if token in {"logger", "lo", "c"} and _is_logger_followed_by_method(pattern, match.end()):
        return r"(?P<logger>.+\S)\s*"
    if token in FIELD_PATTERNS:
        return FIELD_PATTERNS[token]
    return PASS_THROUGH_PATTERNS[token]


def _literal_to_regex(literal: str) -> str:
    escaped = re.escape(literal)
    return re.sub(r"\\\s+", r"\\s+", escaped)


def _is_logger_followed_by_method(pattern: str, position: int) -> bool:
    return re.match(r"\.%-?\d*(?:\.\d+)?(?:method|M)(?:\{[^}]*\})?", pattern[position:]) is not None


def _expand_logback_default_placeholders(pattern: str) -> str:
    """Expand supported ${NAME:-default} placeholders used by Logback/Spring Boot."""
    expanded = pattern
    for _ in range(20):
        previous = expanded
        expanded = re.sub(r"\$\{[^{}:]+:-([^{}]*)\}", r"\1", expanded)

        whole_pattern_default = re.fullmatch(r"\$\{[^{}:]+:-(.*)\}", expanded)
        if whole_pattern_default:
            expanded = whole_pattern_default.group(1)

        if expanded == previous:
            break
    return expanded


_WRAPPER_TOKENS = {
    "replace",
    "highlight",
    "cyan",
    "green",
    "yellow",
    "blue",
    "magenta",
    "red",
    "white",
    "black",
    "gray",
    "grey",
    "bold",
    "boldCyan",
    "boldGreen",
    "boldYellow",
    "boldBlue",
    "boldMagenta",
    "boldRed",
    "boldWhite",
}


def _strip_wrapper_converters(pattern: str) -> str:
    """移除包裹型 converter，保留內層可解析 pattern"""
    stripped = pattern
    for _ in range(20):
        next_stripped, changed = _strip_wrapper_converters_once(stripped)
        if not changed or next_stripped == stripped:
            return stripped
        stripped = next_stripped
    return stripped


def _strip_wrapper_converters_once(pattern: str) -> tuple[str, bool]:
    parts = []
    i = 0
    changed = False

    while i < len(pattern):
        if pattern[i] == "%":
            match = re.match(r"%(-?\d*(?:\.\d+)?)?([A-Za-z]+)", pattern[i:])
            if match:
                token = match.group(2)
                end = i + match.end()
                if token in _WRAPPER_TOKENS and end < len(pattern) and pattern[end] == "(":
                    inner_start = end + 1
                    inner_end = _find_closing_delimiter(pattern, inner_start - 1, "(", ")")
                    if inner_end is not None:
                        parts.append(pattern[inner_start:inner_end])
                        i = inner_end + 1
                        i = _skip_optional_brace_blocks(pattern, i)
                        changed = True
                        continue
        parts.append(pattern[i])
        i += 1

    return "".join(parts), changed


def _skip_optional_brace_blocks(pattern: str, position: int) -> int:
    current = position
    while current < len(pattern) and pattern[current] == "{":
        end = _find_closing_delimiter(pattern, current, "{", "}")
        if end is None:
            break
        current = end + 1
    return current


def _find_closing_delimiter(
    pattern: str,
    opening_index: int,
    open_char: str,
    close_char: str,
) -> int | None:
    depth = 0
    for index in range(opening_index, len(pattern)):
        char = pattern[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def _ensure_required_groups(regex: str) -> None:
    missing = [
        name
        for name in ("timestamp", "thread", "level", "logger", "message")
        if f"?P<{name}>" not in regex
    ]
    if missing:
        raise UnsupportedLogbackPatternError(
            "Logback pattern 缺少必要欄位：" + ", ".join(missing)
        )
