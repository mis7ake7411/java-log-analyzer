from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

from .text_decoding import (
    SOURCE_TEXT_ENCODING,
    SOURCE_TEXT_ERRORS,
    SOURCE_TEXT_NEWLINE,
    normalize_decoded_line,
)
from dataclasses import dataclass
from collections.abc import Iterable

from .logback_pattern import compile_logback_pattern
from .log_sources import select_log_source


@dataclass
class LogbackPatternCandidate:
    name: str
    pattern: str
    source: str
    matches: int = 0
    checked: int = 0
    xml_path: str = ""
    error: str | None = None
    previews: tuple[PatternSample, ...] = ()

    @property
    def score(self) -> float:
        if self.checked == 0:
            return 0.0
        return self.matches / self.checked


@dataclass(frozen=True, slots=True)
class PatternSample:
    raw: str
    fields: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class LogbackPatternAssessment:
    status: str
    reason: str
    candidates: tuple[LogbackPatternCandidate, ...]
    selected: LogbackPatternCandidate | None
    checked: int


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
    *,
    selected_files: tuple[str, ...] | None = None,
) -> LogbackPatternCandidate | None:
    assessment = assess_logback_patterns(
        (xml_path,),
        log_dir,
        sample_limit,
        selected_files=selected_files,
    )
    return assessment.selected


def assess_logback_patterns(
    xml_paths: Iterable[str],
    log_dir: str,
    sample_limit: int = 50,
    *,
    selected_files: tuple[str, ...] | None = None,
) -> LogbackPatternAssessment:
    """以凍結的日誌來源評估一份或多份 XML 中的 Pattern。"""
    if sample_limit <= 0:
        raise ValueError("sample_limit 必須大於 0")

    samples = list(_read_log_samples(log_dir, min(sample_limit, 50), selected_files=selected_files))
    candidates: list[LogbackPatternCandidate] = []
    seen_patterns: set[str] = set()
    xml_errors: list[str] = []

    for xml_path in xml_paths:
        try:
            xml_candidates = load_logback_patterns(xml_path)
        except (ET.ParseError, OSError, ValueError) as exc:
            xml_errors.append(_format_xml_error(xml_path, exc))
            continue

        for candidate in xml_candidates:
            if candidate.pattern in seen_patterns:
                continue
            seen_patterns.add(candidate.pattern)
            candidates.append(_assess_candidate(candidate, samples, xml_path))

    return _classify_assessment(tuple(candidates), len(samples), tuple(xml_errors))


def _format_xml_error(xml_path: str, error: Exception) -> str:
    if isinstance(error, ET.ParseError):
        return f"無法解析 Logback XML：{xml_path}: {error}"
    if isinstance(error, FileNotFoundError):
        return f"找不到檔案：{xml_path}"
    return f"無法讀取 Logback XML：{xml_path}: {error}"


def _classify_assessment(
    candidates: tuple[LogbackPatternCandidate, ...],
    checked: int,
    xml_errors: tuple[str, ...],
) -> LogbackPatternAssessment:
    supported = tuple(candidate for candidate in candidates if candidate.error is None)

    if not candidates:
        reason = "Logback XML 中找不到可用的 Logback pattern。"
        if xml_errors:
            reason += " " + "；".join(xml_errors)
        return LogbackPatternAssessment("unsupported", reason, candidates, None, checked)

    if not supported:
        diagnostics = "；".join(
            f"{candidate.name[:80]}：{candidate.error[:200]}"
            for candidate in candidates
            if candidate.error
        )
        suffix = f" 不支援的候選：{diagnostics}" if diagnostics else ""
        return LogbackPatternAssessment(
            "unsupported",
            f"所有 Logback Pattern 都不支援目前的解析器。{suffix} 請檢查錯誤或手動指定 Pattern。",
            candidates,
            None,
            checked,
        )

    if not checked:
        return LogbackPatternAssessment(
            "uncertain",
            "沒有可用的非空日誌樣本，無法確認 Pattern。",
            candidates,
            None,
            0,
        )

    best_matches = max(candidate.matches for candidate in supported)
    best_candidates = tuple(candidate for candidate in supported if candidate.matches == best_matches)
    if best_matches == 0:
        reason = "抽樣日誌沒有命中任何可解析的 Pattern。"
    elif len(best_candidates) > 1:
        reason = f"有 {len(best_candidates)} 個不同的 Pattern 最高命中數平手，需要確認。"
    elif best_candidates[0].score < 0.8:
        reason = f"最高候選只命中 {best_candidates[0].matches}/{best_candidates[0].checked}，低於 80% 信心門檻。"
    else:
        return LogbackPatternAssessment(
            "ready",
            f"唯一最高候選命中 {best_candidates[0].matches}/{best_candidates[0].checked}。",
            candidates,
            best_candidates[0],
            checked,
        )

    return LogbackPatternAssessment("uncertain", reason, candidates, None, checked)


def _assess_candidate(
    candidate: LogbackPatternCandidate,
    samples: list[str],
    xml_path: str,
) -> LogbackPatternCandidate:
    scored = LogbackPatternCandidate(
        candidate.name,
        candidate.pattern,
        candidate.source,
        xml_path=xml_path,
    )
    scored.checked = len(samples)
    try:
        pattern = compile_logback_pattern(candidate.pattern)
    except (ValueError, re.error) as exc:
        scored.error = str(exc)
        return scored

    matched_previews: list[PatternSample] = []
    unmatched_previews: list[PatternSample] = []
    for line in samples:
        match = pattern.match(line)
        if match:
            scored.matches += 1
            if len(matched_previews) < 3:
                matched_previews.append(
                    PatternSample(
                        raw=line.rstrip("\r\n")[:500],
                        fields={key: value[:500] for key, value in match.groupdict().items() if value is not None},
                    )
                )
        elif len(unmatched_previews) < 3:
            unmatched_previews.append(PatternSample(raw=line.rstrip("\r\n")[:500], fields=None))
    scored.previews = tuple(matched_previews + unmatched_previews)
    return scored


def _read_log_samples(
    log_dir: str,
    sample_limit: int,
    *,
    selected_files: tuple[str, ...] | None = None,
) -> Iterable[str]:
    source = select_log_source(log_dir, selected_files=selected_files)
    remaining = sample_limit
    for filename in source.filenames:
        if remaining <= 0:
            break

        filepath = os.path.join(source.root, filename)
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


def _iter_nonempty_lines(filepath: str) -> Iterable[str]:
    with open(
        filepath,
        "r",
        encoding=SOURCE_TEXT_ENCODING,
        errors=SOURCE_TEXT_ERRORS,
        newline=SOURCE_TEXT_NEWLINE,
    ) as file:
        for line in file:
            normalized_line, _has_encoding_error = normalize_decoded_line(line)
            if normalized_line.strip():
                yield normalized_line


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
