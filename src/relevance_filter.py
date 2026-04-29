#!/usr/bin/env python3
"""GFCF-driven pipeline relevance filter v2.1 for weekly literature monitoring.

Architecture: Pipeline-based with hard thresholds (GFCF-first, MRIO as tool)

Key Features:
- Hard threshold: Investment terms AND domain terms required
- Concept deduplication: Only count one match per concept group
- Bonus capping: Method/policy bonuses capped to prevent keyword stuffing
- Field weighting: Title > Abstract
- Consistency check: HIGH priority requires asset type OR methodology match
- Negative keywords: Filter out financial/ESG noise per pipeline
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
    matched_pipeline: str | None
    matched_pipelines: list[str]
    matched_investment_terms: list[str]
    matched_domain_terms: list[str]
    matched_bonus_terms: list[str]
    matched_asset_types: list[str]
    matched_themes: list[str]
    matched_keywords: list[str]
    matched_tiers: list[str]
    is_excluded: bool
    exclusion_reason: str | None
    passed_consistency: bool
    negative_matches: list[str]


@dataclass
class PipelineMatch:
    name: str
    pipeline_id: str
    passed_threshold: bool
    investment_matches: list[str]
    domain_matches: list[str]
    bonus_matches: list[str]
    base_score: int
    bonus_score: int
    total_score: int
    blocked_by_negative: bool


@dataclass
class PipelineConfig:
    name: str
    pipeline_id: str
    priority: int
    investment_patterns: list[re.Pattern]
    domain_patterns: list[re.Pattern]
    bonus_patterns: list[re.Pattern]
    negative_patterns: list[re.Pattern]


@dataclass
class KeywordConfig:
    investment_patterns: list[re.Pattern]
    concept_groups: dict[str, list[str]]
    asset_type_patterns: dict[str, list[re.Pattern]]
    pipelines: dict[str, PipelineConfig]
    theme_patterns: dict[str, list[re.Pattern]]
    theme_scores: dict[str, int]
    consistency_patterns: list[re.Pattern]
    exclusion_patterns: list[re.Pattern]
    title_weight: int
    abstract_weight: int
    asset_type_bonus: int
    method_bonus: int
    method_bonus_cap: int
    policy_bonus: int
    policy_bonus_cap: int
    multi_pipeline_bonus: int
    high_priority_threshold: int
    medium_priority_threshold: int
    consistency_check_enabled: bool


def compile_patterns(keywords: list[str], escape_cn: bool = False) -> list[re.Pattern]:
    patterns = []
    for kw in keywords:
        if escape_cn and not kw.startswith("\\"):
            patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
        else:
            patterns.append(re.compile(kw, re.IGNORECASE))
    return patterns


def load_keyword_config(config_path: Path | None = None) -> KeywordConfig:
    if config_path is None:
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / "config" / "keywords.json"

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    rules = config.get("scoring_rules", {})

    gfcf_vocab = config.get("gfcf_vocabulary", {})
    inv_terms = gfcf_vocab.get("investment_terms", {})
    investment_patterns = compile_patterns(
        inv_terms.get("keywords_en", [])
    ) + compile_patterns(inv_terms.get("keywords_cn", []), escape_cn=True)

    concept_groups = inv_terms.get("concept_groups", {})
    if "note" in concept_groups:
        del concept_groups["note"]

    asset_type_patterns: dict[str, list[re.Pattern]] = {}
    for asset_name, asset_data in gfcf_vocab.get("asset_types", {}).items():
        if asset_name == "description" or not isinstance(asset_data, dict):
            continue
        asset_type_patterns[asset_name] = compile_patterns(
            asset_data.get("keywords_en", [])
        ) + compile_patterns(asset_data.get("keywords_cn", []), escape_cn=True)

    negative_kw = config.get("negative_keywords", {})
    negative_by_pipeline: dict[str, list[re.Pattern]] = {}
    for neg_group, neg_data in negative_kw.items():
        if neg_group == "description" or not isinstance(neg_data, dict):
            continue
        patterns = compile_patterns(neg_data.get("keywords_en", []))
        for pipe_id in neg_data.get("apply_to", []):
            if pipe_id not in negative_by_pipeline:
                negative_by_pipeline[pipe_id] = []
            negative_by_pipeline[pipe_id].extend(patterns)

    pipelines: dict[str, PipelineConfig] = {}
    for pipe_name, pipe_data in config.get("pipelines", {}).items():
        if pipe_name == "description" or not isinstance(pipe_data, dict):
            continue

        domain_key = None
        for key in [
            "environmental_impact_terms",
            "io_method_terms",
            "trade_terms",
            "policy_terms",
        ]:
            if key in pipe_data:
                domain_key = key
                break

        domain_data = pipe_data.get(domain_key, {}) if domain_key else {}
        bonus_data = pipe_data.get("bonus_terms", {})

        pipelines[pipe_name] = PipelineConfig(
            name=pipe_data.get("name", pipe_name),
            pipeline_id=pipe_name,
            priority=pipe_data.get("priority", 99),
            investment_patterns=investment_patterns,
            domain_patterns=(
                compile_patterns(domain_data.get("keywords_en", []))
                + compile_patterns(domain_data.get("keywords_cn", []), escape_cn=True)
            ),
            bonus_patterns=(
                compile_patterns(bonus_data.get("keywords_en", []))
                + compile_patterns(bonus_data.get("keywords_cn", []), escape_cn=True)
            ),
            negative_patterns=negative_by_pipeline.get(pipe_name, []),
        )

    theme_patterns: dict[str, list[re.Pattern]] = {}
    theme_scores: dict[str, int] = {}
    for theme_name, theme_data in config.get("supplementary_themes", {}).items():
        if theme_name == "description" or not isinstance(theme_data, dict):
            continue
        theme_patterns[theme_name] = compile_patterns(
            theme_data.get("keywords_en", [])
        ) + compile_patterns(theme_data.get("keywords_cn", []), escape_cn=True)
        theme_scores[theme_name] = theme_data.get("bonus_score", 3)

    consistency_patterns: list[re.Pattern] = []
    consistency_req = config.get("consistency_requirements", {})
    if consistency_req.get("enabled", False):
        for check in consistency_req.get("checks", []):
            if "keywords_en" in check:
                consistency_patterns.extend(compile_patterns(check["keywords_en"]))

    exclusion_patterns = compile_patterns(config.get("exclusion_patterns", []))

    return KeywordConfig(
        investment_patterns=investment_patterns,
        concept_groups=concept_groups,
        asset_type_patterns=asset_type_patterns,
        pipelines=pipelines,
        theme_patterns=theme_patterns,
        theme_scores=theme_scores,
        consistency_patterns=consistency_patterns,
        exclusion_patterns=exclusion_patterns,
        title_weight=rules.get("title_weight", 8),
        abstract_weight=rules.get("abstract_weight", 5),
        asset_type_bonus=rules.get("asset_type_bonus", 2),
        method_bonus=rules.get("method_bonus", 3),
        method_bonus_cap=rules.get("method_bonus_cap", 6),
        policy_bonus=rules.get("policy_bonus", 2),
        policy_bonus_cap=rules.get("policy_bonus_cap", 6),
        multi_pipeline_bonus=rules.get("multi_pipeline_bonus", 5),
        high_priority_threshold=rules.get("high_priority_threshold", 20),
        medium_priority_threshold=rules.get("medium_priority_threshold", 12),
        consistency_check_enabled=rules.get("consistency_check_enabled", True),
    )


def match_patterns(text: str, patterns: list[re.Pattern]) -> list[str]:
    if not text:
        return []
    matches = []
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            matches.append(found.group(0).lower())
    return matches


def deduplicate_by_concept_groups(
    matches: list[str], concept_groups: dict[str, list[str]]
) -> list[str]:
    """Remove duplicate matches from same concept group, keeping only one per group."""
    if not concept_groups:
        return list(set(matches))

    matched_groups: set[str] = set()
    deduped: list[str] = []
    matches_lower = [m.lower() for m in matches]

    for match in matches_lower:
        group_found = None
        for group_name, group_terms in concept_groups.items():
            for term in group_terms:
                if term.lower() in match or match in term.lower():
                    group_found = group_name
                    break
            if group_found:
                break

        if group_found:
            if group_found not in matched_groups:
                matched_groups.add(group_found)
                deduped.append(match)
        else:
            if match not in deduped:
                deduped.append(match)

    return deduped


def check_exclusion(title: str, patterns: list[re.Pattern]) -> str | None:
    title_lower = title.lower()
    for pattern in patterns:
        if pattern.search(title_lower):
            return f"Matches exclusion pattern: {pattern.pattern}"
    return None


def check_negative_keywords(text: str, patterns: list[re.Pattern]) -> list[str]:
    """Check for negative keyword matches."""
    return match_patterns(text, patterns)


def evaluate_pipeline(
    title: str,
    abstract: str,
    pipeline: PipelineConfig,
    config: KeywordConfig,
) -> PipelineMatch:
    combined = f"{title} {abstract}".lower()
    title_lower = title.lower()
    abstract_lower = abstract.lower() if abstract else ""

    negative_matches = check_negative_keywords(combined, pipeline.negative_patterns)
    if negative_matches:
        return PipelineMatch(
            name=pipeline.name,
            pipeline_id=pipeline.pipeline_id,
            passed_threshold=False,
            investment_matches=[],
            domain_matches=[],
            bonus_matches=[],
            base_score=0,
            bonus_score=0,
            total_score=0,
            blocked_by_negative=True,
        )

    investment_matches = match_patterns(combined, pipeline.investment_patterns)
    investment_deduped = deduplicate_by_concept_groups(
        investment_matches, config.concept_groups
    )

    domain_matches = match_patterns(combined, pipeline.domain_patterns)

    passed_threshold = bool(investment_deduped) and bool(domain_matches)

    bonus_matches = []
    bonus_score = 0
    if passed_threshold:
        bonus_matches = list(set(match_patterns(combined, pipeline.bonus_patterns)))
        raw_bonus = len(bonus_matches) * config.method_bonus
        bonus_score = min(raw_bonus, config.method_bonus_cap)

    base_score = 0
    if passed_threshold:
        inv_title = match_patterns(title_lower, pipeline.investment_patterns)
        inv_abstract = match_patterns(abstract_lower, pipeline.investment_patterns)
        inv_title_deduped = deduplicate_by_concept_groups(
            inv_title, config.concept_groups
        )
        inv_abstract_deduped = deduplicate_by_concept_groups(
            inv_abstract, config.concept_groups
        )
        base_score += len(inv_title_deduped) * config.title_weight
        base_score += len(inv_abstract_deduped) * config.abstract_weight

        dom_title = match_patterns(title_lower, pipeline.domain_patterns)
        dom_abstract = match_patterns(abstract_lower, pipeline.domain_patterns)
        base_score += len(set(dom_title)) * config.title_weight
        base_score += len(set(dom_abstract)) * config.abstract_weight

    return PipelineMatch(
        name=pipeline.name,
        pipeline_id=pipeline.pipeline_id,
        passed_threshold=passed_threshold,
        investment_matches=list(set(investment_deduped)),
        domain_matches=list(set(domain_matches)),
        bonus_matches=bonus_matches,
        base_score=base_score,
        bonus_score=bonus_score,
        total_score=base_score + bonus_score if passed_threshold else 0,
        blocked_by_negative=False,
    )


def check_consistency(
    text: str,
    asset_type_patterns: dict[str, list[re.Pattern]],
    consistency_patterns: list[re.Pattern],
) -> tuple[bool, list[str]]:
    """Check if paper passes consistency requirements for HIGH priority."""
    matched_assets = []
    for asset_name, patterns in asset_type_patterns.items():
        if match_patterns(text, patterns):
            matched_assets.append(asset_name)

    if matched_assets:
        return True, matched_assets

    if match_patterns(text, consistency_patterns):
        return True, []

    return False, []


def score_paper(
    title: str,
    abstract: str | None,
    config: KeywordConfig,
) -> RelevanceResult:
    abstract = abstract or ""
    combined = f"{title} {abstract}".lower()

    exclusion_reason = check_exclusion(title, config.exclusion_patterns)
    if exclusion_reason:
        return RelevanceResult(
            doi="",
            title=title,
            score=0,
            priority="EXCLUDED",
            matched_pipeline=None,
            matched_pipelines=[],
            matched_investment_terms=[],
            matched_domain_terms=[],
            matched_bonus_terms=[],
            matched_asset_types=[],
            matched_themes=[],
            matched_keywords=[],
            matched_tiers=[],
            is_excluded=True,
            exclusion_reason=exclusion_reason,
            passed_consistency=False,
            negative_matches=[],
        )

    pipeline_results: list[PipelineMatch] = []
    all_negative_matches: list[str] = []

    for pipe_name, pipeline in config.pipelines.items():
        result = evaluate_pipeline(title, abstract, pipeline, config)
        if result.blocked_by_negative:
            all_negative_matches.extend(
                match_patterns(combined, pipeline.negative_patterns)
            )
        if result.passed_threshold:
            pipeline_results.append(result)

    if not pipeline_results:
        return RelevanceResult(
            doi="",
            title=title,
            score=0,
            priority="NONE",
            matched_pipeline=None,
            matched_pipelines=[],
            matched_investment_terms=[],
            matched_domain_terms=[],
            matched_bonus_terms=[],
            matched_asset_types=[],
            matched_themes=[],
            matched_keywords=[],
            matched_tiers=[],
            is_excluded=False,
            exclusion_reason=None,
            passed_consistency=False,
            negative_matches=list(set(all_negative_matches)),
        )

    pipeline_results.sort(key=lambda x: (-x.total_score,))
    primary_pipeline = pipeline_results[0]

    all_investment = []
    all_domain = []
    all_bonus = []
    total_score = primary_pipeline.total_score

    for pr in pipeline_results:
        all_investment.extend(pr.investment_matches)
        all_domain.extend(pr.domain_matches)
        all_bonus.extend(pr.bonus_matches)

    if len(pipeline_results) > 1:
        total_score += (len(pipeline_results) - 1) * config.multi_pipeline_bonus

    matched_asset_types = []
    for asset_name, patterns in config.asset_type_patterns.items():
        if match_patterns(combined, patterns):
            matched_asset_types.append(asset_name)
            total_score += config.asset_type_bonus

    matched_themes = []
    for theme_name, patterns in config.theme_patterns.items():
        if match_patterns(combined, patterns):
            matched_themes.append(theme_name)
            total_score += config.theme_scores.get(theme_name, 3)

    passed_consistency, consistency_assets = check_consistency(
        combined,
        config.asset_type_patterns,
        config.consistency_patterns,
    )

    if total_score >= config.high_priority_threshold:
        if config.consistency_check_enabled and not passed_consistency:
            priority = "MEDIUM"
        else:
            priority = "HIGH"
    elif total_score >= config.medium_priority_threshold:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    all_keywords = list(set(all_investment + all_domain + all_bonus))
    matched_pipeline_names = [pr.name for pr in pipeline_results]

    return RelevanceResult(
        doi="",
        title=title,
        score=total_score,
        priority=priority,
        matched_pipeline=primary_pipeline.name,
        matched_pipelines=matched_pipeline_names,
        matched_investment_terms=list(set(all_investment)),
        matched_domain_terms=list(set(all_domain)),
        matched_bonus_terms=list(set(all_bonus)),
        matched_asset_types=matched_asset_types,
        matched_themes=matched_themes,
        matched_keywords=all_keywords,
        matched_tiers=matched_pipeline_names,
        is_excluded=False,
        exclusion_reason=None,
        passed_consistency=passed_consistency,
        negative_matches=list(set(all_negative_matches)),
    )


def filter_papers(
    papers: list[dict[str, Any]],
    config: KeywordConfig | None = None,
    min_priority: str = "LOW",
) -> list[tuple[dict[str, Any], RelevanceResult]]:
    """Filter papers using the pipeline system with v2.1 enhancements."""
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
