"""OpenAlex provider adapter."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from literature_monitor.compat import SRC  # noqa: F401
from literature_monitor.core.models import (
    Paper,
    PaperSourceRecord,
    ProviderPaper,
    SearchRequest,
)
from paper_utils import clean_abstract, clean_title, fetch_url, normalize_doi

OPENALEX_WORKS = "https://api.openalex.org/works"
OPENALEX_PER_PAGE_MAX = 200


class OpenAlexFetchError(RuntimeError):
    pass


def rebuild_inverted_abstract(value: dict[str, list[int]] | None) -> str | None:
    if not value:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        for position in positions:
            positioned.append((int(position), word))
    if not positioned:
        return None
    return clean_abstract(" ".join(word for _, word in sorted(positioned)))


def fetch_openalex(
    request: SearchRequest, issns: list[str], *, timeout_s: int = 30
) -> list[ProviderPaper]:
    issn_chunks = chunked(issns, 50) if issns else [[]]
    all_results: list[ProviderPaper] = []
    seen_ids: set[str] = set()
    for chunk in issn_chunks:
        for record in _fetch_openalex_chunk(request, chunk, timeout_s=timeout_s):
            work_id = (
                record.source_record.source_work_id
                or record.paper.doi
                or record.paper.title
            )
            if work_id in seen_ids:
                continue
            seen_ids.add(work_id)
            all_results.append(record)
            if len(all_results) >= request.max_results_per_source:
                return all_results[: request.max_results_per_source]
    return all_results[: request.max_results_per_source]


def _fetch_openalex_chunk(
    request: SearchRequest, issns: list[str], *, timeout_s: int = 30
) -> list[ProviderPaper]:
    per_page = min(max(request.max_results_per_source, 1), OPENALEX_PER_PAGE_MAX)
    results: list[ProviderPaper] = []
    cursor = "*"
    seen_ids: set[str] = set()

    while len(results) < request.max_results_per_source:
        query_parts = [
            f"per-page={per_page}",
            "sort=publication_date:desc",
            f"cursor={urllib.parse.quote(cursor)}",
        ]
        # Only use keyword search when NO ISSNs are available.
        # With ISSNs, we fetch all papers from those journals (post-filter with keywords).
        # This matches the legacy strategy and avoids Crossref/OpenAlex
        # keyword matching issues with complex query strings.
        if not issns:
            provider_query = request.provider_query_text or request.query_text
            if provider_query:
                query_parts.append(f"search={urllib.parse.quote(provider_query)}")

        filters = []
        if request.date_from:
            filters.append(f"from_publication_date:{request.date_from}")
        if request.date_to:
            filters.append(f"to_publication_date:{request.date_to}")
        if issns:
            # OpenAlex accepts pipe-separated OR values in many filters.
            filters.append(f"primary_location.source.issn:{'|'.join(issns[:50])}")
        if filters:
            encoded_filters = urllib.parse.quote(",".join(filters), safe=":,|")
            query_parts.append(f"filter={encoded_filters}")

        url = f"{OPENALEX_WORKS}?{'&'.join(query_parts)}"
        status, body = fetch_url(url, timeout_s=timeout_s)
        if status is None or status < 200 or status >= 300:
            preview = body.decode("utf-8", errors="replace")[:300] if body else ""
            raise OpenAlexFetchError(f"OpenAlex HTTP {status}: {preview}")
        data = json.loads(body.decode("utf-8", errors="replace"))
        items = data.get("results", [])
        if not items:
            break

        for item in items:
            work_id = item.get("id", "")
            if work_id in seen_ids:
                continue
            seen_ids.add(work_id)
            parsed = parse_openalex_work(item)
            if parsed is not None:
                results.append(parsed)

        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return results[: request.max_results_per_source]


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def parse_openalex_work(item: dict[str, Any]) -> ProviderPaper | None:
    title = clean_title(item.get("title") or item.get("display_name"))
    if not title:
        return None
    doi = item.get("doi")
    normalized = normalize_doi(doi or "")
    doi_value = normalized.full if normalized else None
    source = (item.get("primary_location") or {}).get("source") or {}
    primary = item.get("primary_location") or {}
    open_access = item.get("open_access") or {}
    publication_year = item.get("publication_year")
    publication_date = item.get("publication_date")
    authors = []
    for authorship in item.get("authorships", []):
        author = authorship.get("author") or {}
        if author.get("display_name"):
            authors.append(author["display_name"])
    topics = [
        concept.get("display_name")
        for concept in item.get("concepts", [])
        if concept.get("display_name")
    ][:12]
    for topic in item.get("topics", []):
        if topic.get("display_name") and topic["display_name"] not in topics:
            topics.append(topic["display_name"])

    issn = None
    issns = source.get("issn") or source.get("issn_l")
    if isinstance(issns, list) and issns:
        issn = issns[0]
    elif isinstance(issns, str):
        issn = issns

    paper = Paper(
        doi=doi_value,
        title=title,
        authors=authors,
        journal=source.get("display_name"),
        issn=issn,
        publisher=source.get("host_organization_name"),
        year=str(publication_year) if publication_year else None,
        publication_date=publication_date,
        abstract=rebuild_inverted_abstract(item.get("abstract_inverted_index")),
        url=primary.get("landing_page_url") or item.get("id"),
        oa_url=primary.get("pdf_url") or open_access.get("oa_url"),
        citation_count=item.get("cited_by_count"),
        topics=topics,
    )
    return ProviderPaper(
        paper=paper,
        source_record=PaperSourceRecord(
            source="openalex",
            source_work_id=item.get("id"),
            doi=doi_value,
            raw=item,
        ),
    )
