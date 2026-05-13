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


def _split_date_range(from_date: str, to_date: str) -> list[tuple[str, str]]:
    """Split a large date range into per-year sub-ranges.

    Crossref cursor pagination returns incomplete results for date ranges
    spanning multiple years. Splitting into per-year queries ensures
    complete coverage (verified: JIE 2017 returns 172/172 per-year,
    but only 189/395 for 2017-2019 combined).
    """
    from datetime import datetime
    start = datetime.strptime(from_date[:10], "%Y-%m-%d")
    end = datetime.strptime(to_date[:10], "%Y-%m-%d")

    ranges = []
    current = start
    while current <= end:
        year_end = datetime(current.year, 12, 31)
        if year_end > end:
            year_end = end
        ranges.append((current.strftime("%Y-%m-%d"), year_end.strftime("%Y-%m-%d")))
        current = datetime(current.year + 1, 1, 1)
    return ranges


def fetch_crossref(request: SearchRequest, issns: list[str]) -> list[ProviderPaper]:
    if not issns:
        if not request.query_text:
            return []
        results = fetch_keyword_papers(request, [])
        return [_from_crossref_result(r) for r in results]

    # Single query with higher per-journal cap.
    # Year-by-year splitting was tested but increased failure rate
    # (more API calls = more chances for any single call to fail).
    # Instead, use max_per_journal=500 which gives Crossref enough
    # rows per batch to cover multi-year ranges in one cursor pass.
    results = fetch_recent_papers(
        issns=issns,
        from_date=request.date_from,
        to_date=request.date_to,
        max_per_journal=500,
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
