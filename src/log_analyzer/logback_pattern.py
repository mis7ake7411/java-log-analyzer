import re
from typing import Pattern


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
    "mdc",
    "X",
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
    "mdc": r".*?",
    "X": r".*?",
    "ex": r".*",
    "throwable": r".*",
    "xEx": r".*",
    "wEx": r".*",
    "wex": r".*",
    "rootException": r".*",
    "rEx": r".*",
}


class UnsupportedLogbackPatternError(ValueError):
    """Raised when a Logback pattern contains unsupported conversion words."""


def compile_logback_pattern(pattern: str) -> Pattern[str]:
    """Compile a supported subset of Logback PatternLayout syntax into a regex."""
    cleaned = _expand_logback_default_placeholders(pattern.strip())
    if not cleaned:
        return DEFAULT_LOGBACK_REGEX

    regex_parts = ["^"]
    pos = 0
    for match in re.finditer(r"%(-?\d*(?:\.\d+)?)?([A-Za-z]+)(?:\{[^}]*\})?", cleaned):
        literal = cleaned[pos:match.start()]
        regex_parts.append(_literal_to_regex(literal))

        token = match.group(2)
        if token not in SUPPORTED_TOKENS:
            raise UnsupportedLogbackPatternError(f"不支援的 Logback pattern token：%{token}")

        if token == "n":
            regex_parts.append(r"\s*")
        elif token in {"logger", "lo", "c"} and _is_logger_followed_by_method(cleaned, match.end()):
            regex_parts.append(r"(?P<logger>.+\S)\s*")
        elif token in FIELD_PATTERNS:
            regex_parts.append(FIELD_PATTERNS[token])
        else:
            regex_parts.append(PASS_THROUGH_PATTERNS[token])
        pos = match.end()

    regex_parts.append(_literal_to_regex(cleaned[pos:]))
    regex_parts.append("$")
    regex = "".join(regex_parts)

    _ensure_required_groups(regex)
    return re.compile(regex)


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
