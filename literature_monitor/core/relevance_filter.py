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
    asset_type_matches: list[str]
    theme_matches: list[str]
    negative_matches: list[str]
    score: int


@dataclass
class PipelineConfig:
    name: str
    pipeline_id: str
    priority: int
    investment_patterns: list[re.Pattern]
    domain_patterns: list[re.Pattern]
    bonus_patterns: list[re.Pattern] = field(default_factory=list)
    negative_patterns: list[re.Pattern] = field(default_factory=list)
    asset_type_patterns: dict[str, list[re.Pattern]] = field(default_factory=dict)
    theme_patterns: dict[str, tuple[list[re.Pattern], int]] = field(default_factory=dict)


@dataclass
class KeywordConfig:
    investment_patterns: list[re.Pattern]
    concept_groups: dict[str, list[str]]
    asset_type_patterns: dict[str, list[re.Pattern]]
    pipelines: dict[str, PipelineConfig]
    negative_keywords: dict[str, list[re.Pattern]]
    exclusion_patterns: list[re.Pattern]
    supplementary_themes: dict[str, tuple[list[re.Pattern], int]]
    consistency_check_enabled: bool
    consistency_patterns: list[re.Pattern]
    scoring_rules: dict[str, Any]


def compile_patterns(keywords: list[str], escape_cn: bool = False) -> list[re.Pattern]:
    import logging
    _logger = logging.getLogger(__name__)
    if escape_cn:
        patterns = []
        for kw in keywords:
            if not kw or not kw.strip():
                continue
            pattern = re.escape(kw.strip())
            patterns.append(re.compile(pattern, re.IGNORECASE))
        return patterns
    patterns = []
    for kw in keywords:
        if not kw or not kw.strip() or kw.startswith("#"):
            continue
        try:
            patterns.append(re.compile(kw, re.IGNORECASE))
        except re.error:
            _logger.warning("Invalid regex pattern, skipping: %s", kw)
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
        negative_keywords=negative_by_pipeline,
        exclusion_patterns=exclusion_patterns,
        supplementary_themes={
            name: (patterns, theme_scores.get(name, 3))
            for name, patterns in theme_patterns.items()
        },
        consistency_check_enabled=consistency_req.get("enabled", False),
        consistency_patterns=consistency_patterns,
        scoring_rules=rules,
    )


def match_patterns(text: str, patterns: list[re.Pattern]) -> list[str]:
    """Return unique matched substrings from pattern list."""
    matches = []
    seen = set()
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            match_str = m.group(0).lower()
            if match_str not in seen:
                seen.add(match_str)
                matches.append(match_str)
    return matches


def deduplicate_by_concept_groups(
    matches: list[str], concept_groups: dict[str, list[str]]
) -> list[str]:
    """Deduplicate matches: only count one match per concept group."""
    if not concept_groups:
        return matches

    matched_groups: set[str] = set()
    deduplicated: list[str] = []

    # Pre-compute lowercase lookup to avoid O(n²) rebuild
    lower_groups: dict[str, set[str]] = {
        gn: {m.lower() for m in gm} for gn, gm in concept_groups.items()
    }

    for match in matches:
        match_lower = match.lower()
        group_found = False
        for group_name, lower_members in lower_groups.items():
            if match_lower in lower_members:
                if group_name not in matched_groups:
                    matched_groups.add(group_name)
                    deduplicated.append(match)
                group_found = True
                break
        if not group_found:
            deduplicated.append(match)

    return deduplicated


def evaluate_pipeline(
    title: str,
    abstract: str | None,
    pipeline: PipelineConfig,
    config: KeywordConfig,
) -> PipelineMatch:
    """Evaluate a single pipeline against paper title+abstract.

    Returns PipelineMatch with threshold check and scores.
    """
    title_lower = title.lower()
    abstract_lower = (abstract or "").lower()
    combined = f"{title_lower} {abstract_lower}"

    # Check negative keywords
    negative_matches = match_patterns(combined, pipeline.negative_patterns)
    if negative_matches:
        return PipelineMatch(
            name=pipeline.name,
            pipeline_id=pipeline.pipeline_id,
            passed_threshold=False,
            investment_matches=[],
            domain_matches=[],
            bonus_matches=[],
            asset_type_matches=[],
            theme_matches=[],
            negative_matches=negative_matches,
            score=-999,
        )

    # Investment term matching with concept deduplication
    title_investment = match_patterns(title_lower, pipeline.investment_patterns)
    abstract_investment = match_patterns(abstract_lower, pipeline.investment_patterns)
    investment_matches = title_investment + [
        m for m in abstract_investment if m not in title_investment
    ]
    investment_deduped = deduplicate_by_concept_groups(
        investment_matches, config.concept_groups
    )

    # Domain term matching
    domain_matches = match_patterns(
        combined, pipeline.domain_patterns
    )

    # Hard threshold check: investment AND domain must both have matches
    passed_threshold = bool(investment_deduped) and bool(domain_matches)

    if not passed_threshold:
        return PipelineMatch(
            name=pipeline.name,
            pipeline_id=pipeline.pipeline_id,
            passed_threshold=False,
            investment_matches=investment_deduped,
            domain_matches=domain_matches,
            bonus_matches=[],
            asset_type_matches=[],
            theme_matches=[],
            negative_matches=negative_matches,
            score=0,
        )

    # Bonus term matching
    bonus_matches = match_patterns(combined, pipeline.bonus_patterns)

    # Asset type matching
    asset_type_matches: list[str] = []
    for asset_name, asset_patterns in pipeline.asset_type_patterns.items():
        if match_patterns(combined, asset_patterns):
            asset_type_matches.append(asset_name)

    # Theme matching
    theme_matches: list[str] = []
    for theme_name, (theme_pats, _score) in pipeline.theme_patterns.items():
        if match_patterns(combined, theme_pats):
            theme_matches.append(theme_name)

    # Score calculation
    rules = config.scoring_rules
    inv_title_weight = rules.get("investment_title_weight", 20)
    inv_abstract_weight = rules.get("investment_abstract_weight", 12)
    dom_title_weight = rules.get("domain_title_weight", 5)
    dom_abstract_weight = rules.get("domain_abstract_weight", 3)
    method_bonus = rules.get("method_bonus", 3)
    policy_bonus = rules.get("policy_bonus", 2)
    asset_type_bonus = rules.get("asset_type_bonus", 2)
    multi_pipeline_bonus = rules.get("multi_pipeline_bonus", 5)
    method_bonus_cap = rules.get("method_bonus_cap", 6)
    policy_bonus_cap = rules.get("policy_bonus_cap", 6)

    # Base score: investment terms weighted much higher than domain terms
    title_inv_count = len(match_patterns(title_lower, pipeline.investment_patterns))
    title_domain_count = len(match_patterns(title_lower, pipeline.domain_patterns))
    abstract_inv_count = len(match_patterns(abstract_lower, pipeline.investment_patterns))
    abstract_domain_count = len(match_patterns(abstract_lower, pipeline.domain_patterns))

    base_score = (
        title_inv_count * inv_title_weight
        + abstract_inv_count * inv_abstract_weight
        + title_domain_count * dom_title_weight
        + abstract_domain_count * dom_abstract_weight
    )

    # Bonus score
    bonus_score = 0
    method_bonus_total = 0
    policy_bonus_total = 0
    for bm in bonus_matches:
        if any(
            kw.lower() in bm
            for kw in [
                "method",
                "framework",
                "model",
                "tool",
                "software",
                "methodology",
                "approach",
                "technique",
            ]
        ):
            method_bonus_total += method_bonus
        elif any(
            kw.lower() in bm
            for kw in ["policy", "strategy", "regulation", "scenario"]
        ):
            policy_bonus_total += policy_bonus
        else:
            method_bonus_total += method_bonus
    method_bonus_total = min(method_bonus_total, method_bonus_cap)
    policy_bonus_total = min(policy_bonus_total, policy_bonus_cap)
    bonus_score = method_bonus_total + policy_bonus_total

    # Asset type bonus
    asset_bonus = len(asset_type_matches) * asset_type_bonus

    # Theme bonus
    theme_bonus = sum(
        score
        for theme_name, (_pats, score) in config.supplementary_themes.items()
        if theme_name in theme_matches
    )

    total = base_score + bonus_score + asset_bonus + theme_bonus
    return PipelineMatch(
        name=pipeline.name,
        pipeline_id=pipeline.pipeline_id,
        passed_threshold=True,
        investment_matches=investment_deduped,
        domain_matches=domain_matches,
        bonus_matches=bonus_matches,
        asset_type_matches=asset_type_matches,
        theme_matches=theme_matches,
        negative_matches=negative_matches,
        score=total,
    )


def check_consistency(
    title: str,
    abstract: str | None,
    asset_type_matches: list[str],
    consistency_patterns: list[re.Pattern],
) -> tuple[bool, list[str]]:
    """Check consistency: paper must match asset type OR methodology terms."""
    if asset_type_matches:
        return True, asset_type_matches

    combined = f"{title.lower()} {(abstract or '').lower()}"
    matches = match_patterns(combined, consistency_patterns)
    return bool(matches), matches


def score_paper(
    title: str, abstract: str | None, config: KeywordConfig
) -> RelevanceResult:
    """Score a paper using the GFCF-driven pipeline system.

    Returns a RelevanceResult with score, priority, and detailed match info.
    """
    title_lower = title.lower()
    abstract_lower = (abstract or "").lower()
    combined = f"{title_lower} {abstract_lower}"

    # Check exclusion patterns
    exclusion_matches = match_patterns(combined, config.exclusion_patterns)
    if exclusion_matches:
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
            exclusion_reason=exclusion_matches[0],
            passed_consistency=False,
            negative_matches=[],
        )

    # Evaluate all pipelines
    best_match: PipelineMatch | None = None
    all_passed: list[PipelineMatch] = []

    sorted_pipelines = sorted(
        config.pipelines.values(), key=lambda p: p.priority
    )

    for pipeline in sorted_pipelines:
        match = evaluate_pipeline(title, abstract, pipeline, config)
        if match.passed_threshold:
            all_passed.append(match)
            if best_match is None or match.score > best_match.score:
                best_match = match

    if not best_match:
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
            negative_matches=[],
        )

    # Multi-pipeline bonus
    rules = config.scoring_rules
    multi_bonus = rules.get("multi_pipeline_bonus", 5)
    if len(all_passed) > 1:
        best_match.score += (len(all_passed) - 1) * multi_bonus

    # Asset type matching (global, not per-pipeline)
    asset_type_matches: list[str] = []
    for asset_name, asset_patterns in config.asset_type_patterns.items():
        if match_patterns(combined, asset_patterns):
            asset_type_matches.append(asset_name)

    # Supplementary theme matching
    theme_matches: list[str] = []
    theme_bonus = 0
    for theme_name, (theme_pats, score) in config.supplementary_themes.items():
        if match_patterns(combined, theme_pats):
            theme_matches.append(theme_name)
            theme_bonus += score

    best_match.score += (
        len(asset_type_matches) * rules.get("asset_type_bonus", 2) + theme_bonus
    )

    # Collect all matched keywords
    all_keywords = set()
    all_keywords.update(best_match.investment_matches)
    all_keywords.update(best_match.domain_matches)
    all_keywords.update(best_match.bonus_matches)
    all_keywords.update(asset_type_matches)
    all_keywords.update(theme_matches)

    # Determine priority
    rules = config.scoring_rules
    high_threshold = rules.get("high_threshold", 20)
    medium_threshold = rules.get("medium_threshold", 12)
    consistency_check_enabled = config.consistency_check_enabled

    # Consistency check for HIGH priority
    passed_consistency = True
    consistency_matches: list[str] = []
    if best_match.score >= high_threshold and consistency_check_enabled:
        passed_consistency, consistency_matches = check_consistency(
            title, abstract, asset_type_matches, config.consistency_patterns
        )

    if best_match.score >= high_threshold:
        priority = "HIGH" if passed_consistency else "MEDIUM"
    elif best_match.score >= medium_threshold:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    matched_pipelines = [m.name for m in all_passed]
    matched_tiers = list(set(matched_pipelines))

    return RelevanceResult(
        doi="",
        title=title,
        score=best_match.score,
        priority=priority,
        matched_pipeline=best_match.name,
        matched_pipelines=matched_pipelines,
        matched_investment_terms=best_match.investment_matches,
        matched_domain_terms=best_match.domain_matches,
        matched_bonus_terms=best_match.bonus_matches,
        matched_asset_types=asset_type_matches,
        matched_themes=theme_matches,
        matched_keywords=list(all_keywords),
        matched_tiers=matched_tiers,
        is_excluded=False,
        exclusion_reason=None,
        passed_consistency=passed_consistency,
        negative_matches=best_match.negative_matches,
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
