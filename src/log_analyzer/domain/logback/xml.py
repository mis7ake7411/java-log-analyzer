from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from contextlib import closing
from itertools import islice

from ..text_decoding import (
    SOURCE_TEXT_ENCODING,
    SOURCE_TEXT_ERRORS,
    SOURCE_TEXT_NEWLINE,
    normalize_decoded_line,
)
from dataclasses import dataclass
from collections.abc import Iterable

from .mdc import extract_mdc, is_mdc_capture_group
from .pattern import compile_logback_pattern
from ..log_sources import select_log_source


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
    file_scores: tuple[PatternFileScore, ...] = ()

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
class PatternFileScore:
    filename: str
    matches: int
    checked: int


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


def preview_logback_pattern(
    pattern: str | None,
    log_dir: str,
    *,
    selected_files: tuple[str, ...] | None = None,
    sample_limit: int = 50,
) -> LogbackPatternCandidate:
    """以既有抽樣與評分流程預覽手動或預設 Pattern 的欄位擷取結果。

    Args:
        pattern: 手動 Pattern；None 使用既有內建預設解析器。
        log_dir: 日誌資料夾或單一日誌檔路徑。
        selected_files: 本次限定讀取的檔名；None 使用來源預設範圍。
        sample_limit: 抽樣數量，最多使用既有的五十筆上限。

    Returns:
        含命中數、錯誤、樣本預覽與逐檔分數的 Pattern 候選。

    Raises:
        ValueError: 抽樣數量非正數，或手動 Pattern 為空白。
        OSError: 來源檔案或資料夾無法讀取。
    """
    if sample_limit <= 0:
        raise ValueError("sample_limit 必須大於 0")
    if pattern is not None and not pattern.strip():
        raise ValueError("手動 Pattern 不可為空白")

    source = select_log_source(log_dir, selected_files=selected_files)
    samples = list(
        _read_source_samples(
            source.root,
            min(sample_limit, 50),
            selected_files=source.filenames,
        )
    )
    candidate_pattern = "" if pattern is None else pattern
    return _assess_candidate(
        LogbackPatternCandidate("Pattern", candidate_pattern, "preview"),
        samples,
        "",
        source.filenames,
    )


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

    source = select_log_source(log_dir, selected_files=selected_files)
    samples = list(_read_source_samples(source.root, min(sample_limit, 50), selected_files=source.filenames))
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
            candidates.append(_assess_candidate(candidate, samples, xml_path, source.filenames))

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
        weak_files = [
            item.filename for item in best_candidates[0].file_scores
            if item.checked and item.matches / item.checked < 0.8
        ]
        if weak_files:
            names = "、".join(weak_files[:5])
            suffix = f"（共 {len(weak_files)} 個）" if len(weak_files) > 5 else ""
            return LogbackPatternAssessment(
                "uncertain", f"整體命中率達門檻，但個別檔案低於 80%：{names}{suffix}。",
                candidates, None, checked,
            )
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
    samples: list[tuple[str, str]],
    xml_path: str,
    filenames: tuple[str, ...],
) -> LogbackPatternCandidate:
    scored = LogbackPatternCandidate(
        candidate.name,
        candidate.pattern,
        candidate.source,
        xml_path=xml_path,
    )
    scored.checked = len(samples)
    counts = {filename: [0, 0] for filename in filenames}
    try:
        pattern = compile_logback_pattern(candidate.pattern)
    except (ValueError, re.error) as exc:
        scored.error = str(exc)
        return scored

    matched_previews: list[PatternSample] = []
    unmatched_previews: list[PatternSample] = []
    for filename, line in samples:
        counts[filename][1] += 1
        match = pattern.match(line)
        if match:
            scored.matches += 1
            counts[filename][0] += 1
            if len(matched_previews) < 3:
                matched_previews.append(
                    PatternSample(
                        raw=line.rstrip("\r\n")[:500],
                        fields={
                            **{
                                key: value[:500]
                                for key, value in match.groupdict().items()
                                if value is not None and not is_mdc_capture_group(key)
                            },
                            **{
                                f"MDC.{key}": value[:500]
                                for key, value in extract_mdc(match).items()
                            },
                        },
                    )
                )
        elif len(unmatched_previews) < 3:
            unmatched_previews.append(PatternSample(raw=line.rstrip("\r\n")[:500], fields=None))
    scored.previews = tuple(matched_previews + unmatched_previews)
    scored.file_scores = tuple(
        PatternFileScore(filename, matches, checked)
        for filename, (matches, checked) in counts.items()
    )
    return scored


def _read_log_samples(
    log_dir: str,
    sample_limit: int,
    *,
    selected_files: tuple[str, ...] | None = None,
) -> Iterable[str]:
    for _, line in _read_source_samples(log_dir, sample_limit, selected_files=selected_files):
        yield line


def _read_source_samples(
    log_dir: str,
    sample_limit: int,
    *,
    selected_files: tuple[str, ...] | None = None,
) -> Iterable[tuple[str, str]]:
    source = select_log_source(log_dir, selected_files=selected_files)
    remaining = sample_limit
    for index, filename in enumerate(source.filenames):
        if remaining <= 0:
            break
        # 為後續檔案保留額度；空檔／短檔未用完的額度交給後續檔案。
        quota = max(1, remaining // (len(source.filenames) - index))
        filepath = os.path.join(source.root, filename)
        with closing(_iter_nonempty_lines(filepath)) as lines:
            for line in islice(lines, quota):
                yield filename, line
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
