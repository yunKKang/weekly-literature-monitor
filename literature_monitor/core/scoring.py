"""Hybrid scoring with legacy rules plus local text relevance."""

from __future__ import annotations

import math
from pathlib import Path
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone

from literature_monitor.config import CONFIG_DIR
from literature_monitor.core.relevance_filter import load_keyword_config, score_paper

from .models import KeywordHit, Paper, ScoreResult, SearchRequest

LEVEL_HIGH = 70
LEVEL_MEDIUM = 35


def score_for_request(
    conn: sqlite3.Connection,
    paper_id: int,
    paper: Paper,
    request: SearchRequest,
    selected_issns: set[str],
) -> ScoreResult:
    keyword_config = load_keyword_config(Path(__file__).resolve().parent.parent.parent / "config" / "keywords.json")
    legacy = score_paper(paper.title, paper.abstract, keyword_config)
    is_gfcf_pool = should_apply_legacy_gfcf(request)
    if not is_gfcf_pool:
        legacy.score = 0
        legacy.priority = "LOW"
        legacy.matched_keywords = []
        legacy.matched_pipelines = []
        legacy.matched_asset_types = []
        legacy.matched_themes = []
        legacy.exclusion_reason = None
        legacy.negative_matches = []

    # For GFCF pools: if the paper failed ALL pipelines (legacy.score == 0),
    # skip dynamic keyword scoring entirely. Without this guard, generic terms
    # like "carbon" + "cement" produce false positives for papers that have
    # nothing to do with capital formation or GFCF research.
    if is_gfcf_pool and legacy.score == 0:
        dynamic_hits: list[KeywordHit] = []
        dynamic_score = 0.0
        negative_penalty = 0.0
    else:
        dynamic_hits = collect_keyword_hits(paper, request)
        dynamic_score = sum(hit.weight for hit in dynamic_hits)
        negative_penalty = negative_keyword_penalty(paper, request.negative_keywords)

    rule_score = max(0.0, float(legacy.score) + dynamic_score - negative_penalty)
    text_score = text_relevance(conn, paper_id, request.query_text)
    recency_score = recency_bonus(paper.publication_date or paper.year)
    journal_score = 5.0 if paper.issn and paper.issn in selected_issns else 0.0

    total_score = rule_score * 1.0 + text_score * 0.6 + recency_score + journal_score
    level = relevance_level(total_score, legacy.priority)

    hits = [
        *dynamic_hits,
        *legacy_keyword_hits(paper, legacy.matched_keywords),
    ]
    matched_keywords = sorted(
        {hit.keyword for hit in hits} | set(legacy.matched_keywords)
    )
    breakdown = {
        "legacy_priority": legacy.priority,
        "legacy_score": legacy.score,
        "dynamic_keyword_score": dynamic_score,
        "negative_penalty": negative_penalty,
        "text_score": text_score,
        "recency_score": recency_score,
        "journal_score": journal_score,
        "exclusion_reason": legacy.exclusion_reason,
        "negative_matches": legacy.negative_matches,
        "matched_asset_types": legacy.matched_asset_types,
        "matched_keywords": matched_keywords,
        "matched_themes": legacy.matched_themes,
    }
    return ScoreResult(
        total_score=round(total_score, 3),
        relevance_level=level,
        rule_score=round(rule_score, 3),
        text_score=round(text_score, 3),
        recency_score=round(recency_score, 3),
        journal_score=round(journal_score, 3),
        matched_pipelines=legacy.matched_pipelines,
        matched_keywords=matched_keywords,
        keyword_hits=hits,
        breakdown=breakdown,
    )


def collect_keyword_hits(paper: Paper, request: SearchRequest) -> list[KeywordHit]:
    hits: list[KeywordHit] = []
    fields = {
        "title": paper.title,
        "abstract": paper.abstract or "",
        "journal": paper.journal or "",
        "topic": " ".join(paper.topics),
    }
    for keyword in request.query_terms:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        for field, text in fields.items():
            if not text or not pattern.search(text):
                continue
            weight = {"title": 12, "abstract": 7, "journal": 4, "topic": 5}[field]
            hits.append(
                KeywordHit(
                    keyword=keyword,
                    field=field,
                    hit_type="user_keyword",
                    weight=weight,
                    snippet=snippet(text, keyword),
                )
            )
    return hits


def should_apply_legacy_gfcf(request: SearchRequest) -> bool:
    if not request.journal_pool_ids:
        return False
    legacy_pool_markers = (
        "main_pool",
        "gfcf",
        "sna",
        "capital",
        "built_environment",
        "digital",
    )
    return any(
        any(marker in pool_id for marker in legacy_pool_markers)
        for pool_id in request.journal_pool_ids
    )


def legacy_keyword_hits(paper: Paper, keywords: Iterable[str]) -> list[KeywordHit]:
    hits = []
    text_by_field = {
        "title": paper.title,
        "abstract": paper.abstract or "",
        "journal": paper.journal or "",
        "topic": " ".join(paper.topics),
    }
    for keyword in keywords:
        for field, text in text_by_field.items():
            if keyword and keyword.lower() in text.lower():
                hits.append(
                    KeywordHit(
                        keyword=keyword,
                        field=field,
                        hit_type="pipeline_keyword",
                        weight=2,
                        snippet=snippet(text, keyword),
                    )
                )
                break
    return hits


def negative_keyword_penalty(paper: Paper, negative_keywords: list[str]) -> float:
    if not negative_keywords:
        return 0.0
    combined = " ".join(
        [paper.title, paper.abstract or "", paper.journal or "", " ".join(paper.topics)]
    ).lower()
    return sum(20.0 for kw in negative_keywords if kw and kw.lower() in combined)


def text_relevance(conn: sqlite3.Connection, paper_id: int, query: str) -> float:
    if not query.strip():
        return 0.0
    tokens = [token for token in re.findall(r"[\w\u4e00-\u9fff]+", query) if token]
    if not tokens:
        return 0.0
    fts_query = " OR ".join(f'"{t}"' for t in tokens[:8])
    try:
        row = conn.execute(
            "SELECT bm25(paper_fts) AS rank FROM paper_fts"
            " WHERE rowid = ? AND paper_fts MATCH ?",
            (paper_id, fts_query),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    if not row:
        return 0.0
    # bm25 is lower-is-better and often negative. Convert to a small positive boost.
    rank = float(row["rank"])
    return min(30.0, abs(rank) * 10.0)


def recency_bonus(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        if len(value) == 4:
            published = datetime(int(value), 1, 1, tzinfo=timezone.utc)
        else:
            published = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    age_days = max((datetime.now(timezone.utc) - published).days, 0)
    return max(0.0, 8.0 - math.log1p(age_days))


def relevance_level(total_score: float, legacy_priority: str) -> str:
    if legacy_priority == "HIGH" or total_score >= LEVEL_HIGH:
        return "HIGH"
    if legacy_priority == "MEDIUM" or total_score >= LEVEL_MEDIUM:
        return "MEDIUM"
    return "LOW"


def snippet(text: str, keyword: str, radius: int = 70) -> str:
    lower = text.lower()
    idx = lower.find(keyword.lower())
    if idx < 0:
        return text[: radius * 2].strip()
    start = max(0, idx - radius)
    end = min(len(text), idx + len(keyword) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"
