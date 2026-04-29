#!/usr/bin/env python3
"""Crossref API client for retrieving recent academic paper metadata.

Crossref (https://www.crossref.org/) provides a free REST API for querying
academic publication metadata. This module is optimized for fetching
recently published papers from specific journals.

API Documentation: https://api.crossref.org/
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from config import config
from paper_utils import (
    DoiInfo,
    SearchParams,
    clean_abstract,
    clean_title,
    fetch_url,
    normalize_doi,
)

logger = logging.getLogger(__name__)

CROSSREF_API_BASE = "https://api.crossref.org"
CROSSREF_WORKS = f"{CROSSREF_API_BASE}/works"


class CrossrefFetchError(RuntimeError):
    """Raised when Crossref returns an unusable response."""


class CrossrefBatchError(RuntimeError):
    """Raised when one or more Crossref batches fail."""


@dataclass
class CrossrefResult:
    """A single paper result from Crossref."""

    doi: str
    title: str
    authors: list[dict[str, str]]
    year: str | None
    journal: str | None
    volume: str | None
    issue: str | None
    pages: str | None
    abstract: str | None
    publisher: str | None
    work_type: str | None
    url: str
    citation_count: int | None
    publication_date: str | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "doi": self.doi,
            "title": self.title,
            "authors": [a.get("name", "") for a in self.authors],
            "year": self.year,
            "journal": self.journal,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "abstract": self.abstract,
            "publisher": self.publisher,
            "work_type": self.work_type,
            "url": self.url,
            "citation_count": self.citation_count,
            "publication_date": self.publication_date,
        }


@dataclass
class CrossrefPage:
    total_results: int
    results: list[CrossrefResult]
    next_cursor: str | None


def build_crossref_query(params: SearchParams) -> str:
    """Build Crossref API query string from search parameters."""
    query_parts = []

    # Main query (optional for date-based searches)
    if params.query:
        query_parts.append(f"query={urllib.parse.quote(params.query)}")

    # Collect all filters
    filters = []

    # Journal filtering via ISSN
    if params.issns:
        for issn in params.issns:
            filters.append(f"issn:{urllib.parse.quote(issn)}")

    # Date range filters - KEY for weekly monitoring
    if params.year_from:
        filters.append(f"from-pub-date:{params.year_from}")
    if params.year_to:
        filters.append(f"until-pub-date:{params.year_to}")

    # Add combined filter parameter
    if filters:
        query_parts.append(f"filter={','.join(filters)}")

    # Result limit
    query_parts.append(f"rows={params.max_results}")

    if params.cursor:
        query_parts.append(f"cursor={urllib.parse.quote(params.cursor)}")

    # Sorting - prioritize newest publications
    sort_map = {
        "relevance": "score",
        "published": "published",
        "citationCount": "is-referenced-by-count",
    }
    sort_by = sort_map.get(params.sort_by, "published")
    query_parts.append(f"sort={sort_by}")
    query_parts.append(f"order={params.sort_order}")

    # Add mailto for polite pool (faster responses)
    query_parts.append(f"mailto={config.CROSSREF_MAILTO}")

    return "&".join(query_parts)


def parse_author(author_data: dict[str, Any]) -> dict[str, str]:
    """Parse author data from Crossref response."""
    given = author_data.get("given", "")
    family = author_data.get("family", "")
    name = author_data.get("name", "")

    if not name:
        if family and given:
            name = f"{family}, {given}"
        elif family:
            name = family
        elif given:
            name = given

    return {
        "name": name,
        "given": given,
        "family": family,
    }


def parse_publication_date(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """Parse publication date from Crossref item.

    Returns:
        Tuple of (year, full_date_string)
    """
    published = item.get("published-print") or item.get("published-online") or {}
    date_parts = published.get("date-parts", [[]])

    if not date_parts or not date_parts[0]:
        return None, None

    parts = date_parts[0]
    year = str(parts[0]) if len(parts) > 0 else None

    # Build full date string
    if len(parts) >= 3:
        full_date = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
    elif len(parts) >= 2:
        full_date = f"{parts[0]:04d}-{parts[1]:02d}-01"
    elif len(parts) >= 1:
        full_date = f"{parts[0]:04d}-01-01"
    else:
        full_date = None

    return year, full_date


def parse_crossref_work(item: dict[str, Any]) -> CrossrefResult | None:
    """Parse a single work item from Crossref response."""
    try:
        # DOI
        doi = item.get("DOI", "")
        doi_info = normalize_doi(doi)
        if not doi_info:
            return None

        # Title
        titles = item.get("title", [])
        title = clean_title(titles[0] if titles else None)
        if not title:
            return None

        # Authors
        authors_raw = item.get("author", [])
        authors = [
            parse_author(a)
            for a in authors_raw
            if a.get("given") or a.get("family") or a.get("name")
        ]

        # Year and date
        year, pub_date = parse_publication_date(item)

        # Journal/Container
        container = item.get("container-title", [])
        journal = container[0] if container else item.get("institution", {}).get("name")

        # Volume, Issue, Pages
        volume = item.get("volume")
        issue = item.get("issue")
        page_str = item.get("page") or item.get("article-number")
        pages = page_str if page_str else None

        # Abstract
        abstract = item.get("abstract")
        if abstract:
            abstract = clean_abstract(abstract)

        # Other fields
        publisher = item.get("publisher")
        work_type = item.get("type")
        url = item.get("URL", "")
        citation_count = item.get("is-referenced-by-count")

        return CrossrefResult(
            doi=doi_info.full,
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            volume=volume,
            issue=issue,
            pages=pages,
            abstract=abstract,
            publisher=publisher,
            work_type=work_type,
            url=url,
            citation_count=citation_count,
            publication_date=pub_date,
            raw=item,
        )

    except Exception as e:
        logger.warning(f"Failed to parse work item: {e}")
        return None


def search_crossref_page(params: SearchParams, *, timeout_s: int = 30) -> CrossrefPage:
    """Search one Crossref page for academic papers.

    Args:
        params: SearchParams with search criteria
        timeout_s: Request timeout in seconds

    Returns:
        CrossrefPage with result metadata and cursor for the next page.
    """
    query_string = build_crossref_query(params)
    url = f"{CROSSREF_WORKS}?{query_string}"

    status, body = fetch_url(url, timeout_s=timeout_s)

    if status is None:
        raise CrossrefFetchError("Crossref request failed before receiving a response")

    if status < 200 or status >= 300:
        body_preview = body.decode("utf-8", errors="replace")[:500] if body else ""
        raise CrossrefFetchError(f"Crossref HTTP {status}: {body_preview}")

    if not body:
        raise CrossrefFetchError("Crossref returned an empty response body")

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise CrossrefFetchError("Crossref returned invalid JSON") from exc

    # Check API status
    message_type = data.get("message-type", "")
    if message_type != "work-list":
        raise CrossrefFetchError(f"Unexpected Crossref message type: {message_type}")

    # Parse results
    message = data.get("message", {})
    total_results = message.get("total-results", 0)
    next_cursor = message.get("next-cursor")
    items = message.get("items", [])

    results = []
    for item in items:
        result = parse_crossref_work(item)
        if result:
            results.append(result)

    return CrossrefPage(
        total_results=total_results,
        results=results,
        next_cursor=next_cursor,
    )


def search_crossref(
    params: SearchParams, *, timeout_s: int = 30
) -> tuple[int, list[CrossrefResult]]:
    """Search Crossref for academic papers."""
    page = search_crossref_page(params, timeout_s=timeout_s)
    return page.total_results, page.results


def fetch_recent_papers(
    issns: list[str],
    from_date: str,
    to_date: str | None = None,
    max_per_journal: int = 50,
    delay_s: float = 0.5,
    batch_size: int | None = None,
) -> list[CrossrefResult]:
    """Fetch recent papers from specified journals.

    Uses batch queries when batch_size is set to reduce API calls.

    Args:
        issns: List of journal ISSNs
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD), defaults to today
        max_per_journal: Maximum papers per journal (used in batch mode as total max)
        delay_s: Delay between API calls (rate limiting)
        batch_size: Number of ISSNs per batch query. None = single ISSN per call.

    Returns:
        List of CrossrefResult objects
    """
    all_results: list[CrossrefResult] = []
    seen_dois: set[str] = set()

    effective_batch_size: int = (
        batch_size if batch_size is not None else config.ISSN_BATCH_SIZE
    )

    for i in range(0, len(issns), effective_batch_size):
        batch = issns[i : i + effective_batch_size]
        errors: list[str] = []
        rows = min(max_per_journal * len(batch), config.MAX_PAPERS_PER_BATCH)
        cursor = "*"
        pages_fetched = 0
        try:
            while True:
                params = SearchParams(
                    query="",
                    issns=batch,
                    year_from=from_date,
                    year_to=to_date,
                    max_results=rows,
                    sort_by="published",
                    sort_order="desc",
                    cursor=cursor,
                )

                page = search_crossref_page(params)
                pages_fetched += 1

                for r in page.results:
                    if r.doi not in seen_dois:
                        seen_dois.add(r.doi)
                        all_results.append(r)

                if not page.results:
                    break
                if not page.next_cursor or page.next_cursor == cursor:
                    break
                if pages_fetched >= config.MAX_CURSOR_PAGES:
                    raise CrossrefFetchError(
                        f"Reached MAX_CURSOR_PAGES={config.MAX_CURSOR_PAGES} for ISSN batch {batch[:3]}"
                    )

                cursor = page.next_cursor

        except Exception as e:
            errors.append(f"ISSN batch {batch[:3]}: {e}")

        if delay_s > 0:
            time.sleep(delay_s)

        if errors:
            raise CrossrefBatchError("; ".join(errors))

    return all_results


def search_with_retry(
    params: SearchParams,
    *,
    timeout_s: int = 30,
    max_retries: int = 3,
    retry_delay_s: float = 2.0,
) -> tuple[int, list[CrossrefResult]]:
    """Search Crossref with automatic retry on failure."""
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return search_crossref(params, timeout_s=timeout_s)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(retry_delay_s * (2**attempt))

    if last_error:
        raise last_error

    return 0, []


def fetch_conference_papers(
    container_titles: list[str],
    from_date: str,
    to_date: str | None = None,
    max_per_conference: int = 50,
    delay_s: float = 0.5,
) -> list[CrossrefResult]:
    """Fetch papers from conference proceedings by container-title.

    Crossref supports container-title filter for conference proceedings.
    This is used for Pool C conferences (SIGCOMM, ISCA, MICRO, etc.).

    Args:
        container_titles: List of conference container titles (short names)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD), defaults to today
        max_per_conference: Maximum papers per conference
        delay_s: Delay between API calls (rate limiting)

    Returns:
        List of CrossrefResult objects
    """
    all_results: list[CrossrefResult] = []
    seen_dois: set[str] = set()

    for container_title in container_titles:
        cursor = "*"
        pages_fetched = 0
        try:
            while True:
                query_parts = [
                    f"query.container-title={urllib.parse.quote(container_title)}",
                    f"rows={max_per_conference}",
                    "sort=published",
                    "order=desc",
                    f"mailto={config.CROSSREF_MAILTO}",
                    f"cursor={urllib.parse.quote(cursor)}",
                ]

                filters = []
                if from_date:
                    filters.append(f"from-pub-date:{from_date}")
                if to_date:
                    filters.append(f"until-pub-date:{to_date}")
                filters.append("type:proceedings-article")

                if filters:
                    query_parts.append(f"filter={','.join(filters)}")

                url = f"{CROSSREF_WORKS}?{'&'.join(query_parts)}"
                status, body = fetch_url(url, timeout_s=30)

                if status is None:
                    raise CrossrefFetchError(
                        f"Crossref request failed for conference {container_title}"
                    )
                if status < 200 or status >= 300:
                    body_preview = (
                        body.decode("utf-8", errors="replace")[:500] if body else ""
                    )
                    raise CrossrefFetchError(
                        f"Crossref HTTP {status} for conference {container_title}: {body_preview}"
                    )
                if not body:
                    raise CrossrefFetchError(
                        f"Crossref returned an empty response for conference {container_title}"
                    )

                data = json.loads(body.decode("utf-8", errors="replace"))
                message = data.get("message", {})
                items = message.get("items", [])
                pages_fetched += 1

                for item in items:
                    result = parse_crossref_work(item)
                    if result and result.doi not in seen_dois:
                        seen_dois.add(result.doi)
                        all_results.append(result)

                next_cursor = message.get("next-cursor")
                if not items:
                    break
                if not next_cursor or next_cursor == cursor:
                    break
                if pages_fetched >= config.MAX_CURSOR_PAGES:
                    raise CrossrefFetchError(
                        f"Reached MAX_CURSOR_PAGES={config.MAX_CURSOR_PAGES} for conference {container_title}"
                    )

                cursor = next_cursor

        except Exception as e:
            raise CrossrefBatchError(f"Conference {container_title}: {e}") from e

        if delay_s > 0:
            time.sleep(delay_s)

    return all_results
