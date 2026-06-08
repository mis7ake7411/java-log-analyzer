from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, List, Optional

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


def load_logback_patterns(xml_path: str) -> List[LogbackPatternCandidate]:
    """Load candidate PatternLayout strings from a logback XML file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    candidates: List[LogbackPatternCandidate] = []

    for element in root.iter():
        tag = _local_name(element.tag)
        if tag == "property":
            name = element.attrib.get("name", "").strip()
            value = element.attrib.get("value", "").strip()
            if name and "PATTERN" in name.upper() and "%" in value:
                _append_unique(candidates, LogbackPatternCandidate(name, value, "property"))
        elif tag.lower() == "pattern":
            text = "".join(element.itertext()).strip()
            if text and "%" in text:
                appender_name = _nearest_appender_name(root, element)
                name = f"{appender_name} Pattern" if appender_name else "Pattern"
                _append_unique(candidates, LogbackPatternCandidate(name, text, "encoder"))

    return candidates


def find_best_logback_pattern(
    xml_path: str,
    log_dir: str,
    sample_limit: int = 50,
) -> Optional[LogbackPatternCandidate]:
    candidates = load_logback_patterns(xml_path)
    samples = list(_read_log_samples(log_dir, sample_limit))
    if not candidates:
        return None

    scored = [_score_candidate(candidate, samples) for candidate in candidates]
    return max(scored, key=lambda candidate: (candidate.matches, candidate.score))


def _score_candidate(
    candidate: LogbackPatternCandidate,
    samples: List[str],
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
    for filename in sorted(os.listdir(log_dir)):
        if remaining <= 0:
            break
        if not filename.endswith(".log"):
            continue

        filepath = os.path.join(log_dir, filename)
        with open(filepath, "r", encoding="utf-8", errors="ignore") as file:
            for line in file:
                if remaining <= 0:
                    break
                if not line.strip():
                    continue
                yield line
                remaining -= 1


def _append_unique(
    candidates: List[LogbackPatternCandidate],
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
