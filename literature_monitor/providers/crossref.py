"""Crossref provider adapter."""

from __future__ import annotations
from pathlib import Path

from literature_monitor.core.utils import get_conference_titles
from literature_monitor.providers.crossref_client import (
    SearchParams,
    fetch_conference_papers,
    fetch_recent_papers,
    search_crossref_page,
)
from literature_monitor.core.models import (
    Paper,
    PaperSourceRecord,
    ProviderPaper,
    SearchRequest,
)

# Backward-compatible path constants (no longer from compat)
from literature_monitor.config import CONFIG_DIR


def fetch_crossref(request: SearchRequest, issns: list[str]) -> list[ProviderPaper]:
    if not issns:
        # No ISSNs — fallback to keyword search (no journal constraint)
        if not request.query_text:
            return []
        results = fetch_keyword_papers(request, [])
        return [_from_crossref_result(r) for r in results]

    # Have ISSNs — always fetch by ISSN (all papers from those journals).
    # Keywords are for post-fetch scoring, NOT for the API query.
    # This matches the legacy weekly_monitor.py strategy.
    results = fetch_recent_papers(
        issns=issns,
        from_date=request.date_from,
        to_date=request.date_to,
        max_per_journal=200,  # per-journal cap, matching legacy default
    )
    return [_from_crossref_result(result) for result in results]


def fetch_keyword_papers(request: SearchRequest, issns: list[str]):
    results = []
    seen_dois = set()
    batch_size = 10
    rows_per_batch = max(1, min(request.max_results_per_source, 100))
    query = request.provider_query_text or request.query_text

    # When no ISSNs, do a single unfiltered keyword search
    batches = [issns[i : i + batch_size] for i in range(0, max(1, len(issns)), batch_size)]
    if not issns:
        batches = [[]]

    for batch in batches:
        params = SearchParams(
            query=query,
            issns=batch or None,
            year_from=request.date_from,
            year_to=request.date_to,
            max_results=rows_per_batch,
            sort_by="relevance",
            sort_order="desc",
        )
        page = search_crossref_page(params)
        for result in page.results:
            key = result.doi or result.title
            if key and key not in seen_dois:
                seen_dois.add(key)
                results.append(result)
                if len(results) >= request.max_results_per_source:
                    return results
    return results


def fetch_crossref_conferences(request: SearchRequest) -> list[ProviderPaper]:
    conferences = get_conference_titles(None, Path(__file__).resolve().parent.parent.parent / "config" / "journals.json")
    if not conferences:
        return []
    results = fetch_conference_papers(
        container_titles=[item["container_title"] for item in conferences],
        from_date=request.date_from,
        to_date=request.date_to,
        max_per_conference=max(1, request.max_results_per_source // len(conferences)),
    )
    return [_from_crossref_result(result) for result in results]


def _from_crossref_result(result) -> ProviderPaper:
    paper = Paper(
        doi=result.doi,
        title=result.title,
        authors=[
            a.get("name", "") for a in result.authors if a.get("name")
        ],
        journal=result.journal,
        publisher=result.publisher,
        year=result.year,
        publication_date=result.publication_date,
        abstract=result.abstract,
        url=result.url,
        citation_count=result.citation_count,
    )
    return ProviderPaper(
        paper=paper,
        source_record=PaperSourceRecord(
            source="crossref",
            source_work_id=result.doi,
            doi=result.doi,
            raw=result.raw,
        ),
    )
