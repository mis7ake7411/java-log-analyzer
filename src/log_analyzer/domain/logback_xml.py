from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from collections.abc import Iterable

from .logback_pattern import UnsupportedLogbackPatternError, compile_logback_pattern


@dataclass
class LogbackPatternCandidate:
    name: str
    pattern: str
    source: str
    matches: int = 0
    checked: int = 0

    @property
    def score(self) -> float:
        if self.checked == 0:
            return 0.0
        return self.matches / self.checked


def load_logback_patterns(xml_path: str) -> list[LogbackPatternCandidate]:
    """Load candidate PatternLayout strings from a logback XML file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    candidates: list[LogbackPatternCandidate] = []

    for element in root.iter():
        tag = _local_name(element.tag)
        if tag == "property":
            candidate = _property_candidate(element)
        elif tag.lower() == "pattern":
            candidate = _encoder_candidate(root, element)
        else:
            candidate = None
        if candidate is not None:
            _append_unique(candidates, candidate)

    return candidates


def find_best_logback_pattern(
    xml_path: str,
    log_dir: str,
    sample_limit: int = 50,
) -> LogbackPatternCandidate | None:
    candidates = load_logback_patterns(xml_path)
    samples = list(_read_log_samples(log_dir, sample_limit))
    if not candidates:
        return None

    scored = [_score_candidate(candidate, samples) for candidate in candidates]
    return max(scored, key=lambda candidate: (candidate.matches, candidate.score))


def _score_candidate(
    candidate: LogbackPatternCandidate,
    samples: list[str],
) -> LogbackPatternCandidate:
    scored = LogbackPatternCandidate(candidate.name, candidate.pattern, candidate.source)
    scored.checked = len(samples)
    try:
        pattern = compile_logback_pattern(candidate.pattern)
    except UnsupportedLogbackPatternError:
        return scored

    scored.matches = sum(1 for line in samples if pattern.match(line))
    return scored


def _read_log_samples(log_dir: str, sample_limit: int) -> Iterable[str]:
    if not os.path.isdir(log_dir):
        return

    remaining = sample_limit
    for filename in _log_filenames(log_dir):
        if remaining <= 0:
            break

        filepath = os.path.join(log_dir, filename)
        for line in _iter_nonempty_lines(filepath):
            if remaining <= 0:
                break
            yield line
            remaining -= 1


def _property_candidate(element: ET.Element) -> LogbackPatternCandidate | None:
    name = element.attrib.get("name", "").strip()
    value = element.attrib.get("value", "").strip()
    if name and "PATTERN" in name.upper() and "%" in value:
        return LogbackPatternCandidate(name, value, "property")
    return None


def _encoder_candidate(root: ET.Element, element: ET.Element) -> LogbackPatternCandidate | None:
    text = "".join(element.itertext()).strip()
    if not text or "%" not in text:
        return None
    appender_name = _nearest_appender_name(root, element)
    name = f"{appender_name} Pattern" if appender_name else "Pattern"
    return LogbackPatternCandidate(name, text, "encoder")


def _log_filenames(log_dir: str) -> Iterable[str]:
    return (filename for filename in sorted(os.listdir(log_dir)) if filename.endswith(".log"))


def _iter_nonempty_lines(filepath: str) -> Iterable[str]:
    with open(filepath, "r", encoding="utf-8", errors="ignore") as file:
        yield from (line for line in file if line.strip())


def _append_unique(
    candidates: list[LogbackPatternCandidate],
    candidate: LogbackPatternCandidate,
) -> None:
    if any(existing.pattern == candidate.pattern for existing in candidates):
        return
    candidates.append(candidate)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _nearest_appender_name(root: ET.Element, target: ET.Element) -> str:
    for appender in root.iter():
        if _local_name(appender.tag) != "appender":
            continue
        if any(descendant is target for descendant in appender.iter()):
            return appender.attrib.get("name", "").strip()
    return ""
