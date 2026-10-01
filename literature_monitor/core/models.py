"""Core domain models for manual literature search."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Paper:
    doi: str | None
    title: str
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    issn: str | None = None
    publisher: str | None = None
    year: str | None = None
    publication_date: str | None = None
    abstract: str | None = None
    url: str | None = None
    oa_url: str | None = None
    citation_count: int | None = None
    topics: list[str] = field(default_factory=list)


@dataclass
class PaperSourceRecord:
    source: str
    source_work_id: str | None
    doi: str | None
    raw: dict[str, Any]


@dataclass
class ProviderPaper:
    paper: Paper
    source_record: PaperSourceRecord


@dataclass
class SearchRequest:
    date_from: str
    date_to: str
    topic_id: str | None = None
    keywords: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    journal_pool_ids: list[str] = field(default_factory=list)
    journal_issns: list[str] = field(default_factory=list)
    include_conferences: bool = False
    min_score: float = 0
    max_results_per_source: int = 10000

    @property
    def query_terms(self) -> list[str]:
        terms = []
        for term in [*self.keywords, *self.synonyms]:
            clean = term.strip()
            if clean and clean not in terms:
                terms.append(clean)
        return terms

    @property
    def query_text(self) -> str:
        return " ".join(self.query_terms)

    @property
    def provider_query_text(self) -> str:
        return " ".join(term.strip() for term in self.keywords if term.strip())


@dataclass
class KeywordHit:
    keyword: str
    field: str
    hit_type: str
    weight: float
    snippet: str | None = None


@dataclass
class ScoreResult:
    total_score: float
    relevance_level: str
    rule_score: float
    text_score: float
    recency_score: float
    journal_score: float
    matched_pipelines: list[str]
    matched_keywords: list[str]
    keyword_hits: list[KeywordHit]
    breakdown: dict[str, Any]


@dataclass
class SearchSummary:
    search_run_id: int
    status: str
    total_fetched: int = 0
    total_after_dedup: int = 0
    total_scored: int = 0
    error_message: str | None = None
