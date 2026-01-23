#!/usr/bin/env python3
"""Multi-tiered relevance filter for weekly literature monitoring.

Uses a scoring system with multiple keyword tiers:
- Tier 1: Core IO/MRIO methods (highest priority)
- Tier 2: Carbon/GHG emissions
- Tier 3: Environmental footprints
- Tier 4: Infrastructure & capital investment
- Tier 5: Trade & globalization
- Tier 6: Climate targets & scenarios
- Tier 7: AI & datacenter environmental impact

Papers matching multiple tiers get bonus scores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RelevanceResult:
    doi: str
    title: str
    score: int
    priority: str
    matched_tiers: list[str]
    matched_keywords: list[str]
    is_excluded: bool
    exclusion_reason: str | None


@dataclass
class KeywordConfig:
    tiers: dict[str, list[re.Pattern]]
    tier_scores: dict[str, int]
    exclusion_patterns: list[re.Pattern]
    multi_tier_bonus: int
    relevant_threshold: int
    high_priority_threshold: int


def load_keyword_config(config_path: Path | None = None) -> KeywordConfig:
    if config_path is None:
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / "config" / "keywords.json"

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    rules = config.get("scoring_rules", {})
    tier_scores = {
        "tier1_core_io": rules.get("tier1_score", 10),
        "tier2_carbon_emissions": rules.get("tier2_score", 5),
        "tier3_environmental_footprints": rules.get("tier3_score", 3),
        "tier4_infrastructure_capital": rules.get("tier4_score", 5),
        "tier5_trade_globalization": rules.get("tier5_score", 5),
        "tier6_climate_targets": rules.get("tier6_score", 3),
        "tier7_ai_datacenter": rules.get("tier7_score", 5),
    }

    tiers: dict[str, list[re.Pattern]] = {}
    for tier_name, tier_data in config.get("tiers", {}).items():
        patterns = []
        for kw in tier_data.get("keywords_en", []):
            try:
                patterns.append(re.compile(kw, re.IGNORECASE))
            except re.error:
                pass
        for kw in tier_data.get("keywords_cn", []):
            try:
                patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
            except re.error:
                pass
        tiers[tier_name] = patterns

    exclusion_patterns = []
    for pattern in config.get("exclusion_patterns", []):
        try:
            exclusion_patterns.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            pass

    return KeywordConfig(
        tiers=tiers,
        tier_scores=tier_scores,
        exclusion_patterns=exclusion_patterns,
        multi_tier_bonus=rules.get("multi_tier_bonus", 5),
        relevant_threshold=rules.get("relevant_threshold", 5),
        high_priority_threshold=rules.get("high_priority_threshold", 15),
    )


def match_patterns(text: str, patterns: list[re.Pattern]) -> list[str]:
    if not text:
        return []

    matches = []
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            matches.append(found.group(0))
    return matches


def check_exclusion(title: str, patterns: list[re.Pattern]) -> str | None:
    title_lower = title.lower()
    for pattern in patterns:
        if pattern.search(title_lower):
            return f"Matches exclusion pattern: {pattern.pattern}"
    return None


def score_paper(
    title: str,
    abstract: str | None,
    config: KeywordConfig,
) -> RelevanceResult:
    combined = f"{title} {abstract or ''}".lower()

    exclusion_reason = check_exclusion(title, config.exclusion_patterns)
    if exclusion_reason:
        return RelevanceResult(
            doi="",
            title=title,
            score=0,
            priority="EXCLUDED",
            matched_tiers=[],
            matched_keywords=[],
            is_excluded=True,
            exclusion_reason=exclusion_reason,
        )

    total_score = 0
    matched_tiers: list[str] = []
    all_matched_keywords: list[str] = []

    for tier_name, patterns in config.tiers.items():
        matches = match_patterns(combined, patterns)
        if matches:
            matched_tiers.append(tier_name)
            all_matched_keywords.extend(matches)
            total_score += config.tier_scores.get(tier_name, 0)

    if len(matched_tiers) > 1:
        total_score += config.multi_tier_bonus * (len(matched_tiers) - 1)

    if total_score >= config.high_priority_threshold:
        priority = "HIGH"
    elif total_score >= config.relevant_threshold:
        priority = "MEDIUM"
    elif total_score > 0:
        priority = "LOW"
    else:
        priority = "NONE"

    return RelevanceResult(
        doi="",
        title=title,
        score=total_score,
        priority=priority,
        matched_tiers=matched_tiers,
        matched_keywords=list(set(all_matched_keywords)),
        is_excluded=False,
        exclusion_reason=None,
    )


def filter_papers(
    papers: list[dict[str, Any]],
    config: KeywordConfig | None = None,
    min_priority: str = "LOW",
) -> list[tuple[dict[str, Any], RelevanceResult]]:
    if config is None:
        config = load_keyword_config()

    priority_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0, "EXCLUDED": -1}
    min_priority_value = priority_order.get(min_priority, 1)

    results: list[tuple[dict[str, Any], RelevanceResult]] = []

    for paper in papers:
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")

        result = score_paper(title, abstract, config)
        result.doi = paper.get("doi", "")

        if priority_order.get(result.priority, 0) >= min_priority_value:
            results.append((paper, result))

    results.sort(key=lambda x: (-x[1].score, x[0].get("publication_date", "") or ""))

    return results
