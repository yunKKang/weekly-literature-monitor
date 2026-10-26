"""Manual literature search orchestration."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from typing import Any

from literature_monitor.core.dedup import deduplicate_records
from literature_monitor.core.journals import resolve_issns
from literature_monitor.core.models import (
    Paper,
    ProviderPaper,
    SearchRequest,
    SearchSummary,
)
from literature_monitor.db import repositories as repo
from literature_monitor.db.connection import connect, get_connection
from literature_monitor.db.schema import init_db
from literature_monitor.providers.crossref import (
    fetch_crossref,
    fetch_crossref_conferences,
)
from literature_monitor.providers.openalex import fetch_openalex

logger = logging.getLogger(__name__)

_DOI_RE = re.compile(r"^10\.\d{4,9}/.+$")


def create_and_run_search(request: SearchRequest) -> SearchSummary:
    with get_connection() as conn:
        init_db(conn)
        search_run_id = repo.create_search_run(
            conn, asdict(request), request.date_from, request.date_to
        )
        return run_search(search_run_id, request, conn=conn)


def run_search(
    search_run_id: int,
    request: SearchRequest,
    conn=None,
) -> SearchSummary:
    own_conn = conn is None
    if own_conn:
        conn = connect()
        init_db(conn)
    try:
        return _run_search_inner(conn, search_run_id, request)
    finally:
        if own_conn:
            conn.close()


def _run_search_inner(
    conn,
    search_run_id: int,
    request: SearchRequest,
) -> SearchSummary:
    repo.update_search_run(conn, search_run_id, status="running", started=True)

    try:
        return _run_search_body(conn, search_run_id, request)
    except Exception as exc:
        logger.exception("Search run %s failed", search_run_id)
        repo.update_search_run(
            conn,
            search_run_id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
            completed=True,
        )
        raise


def _run_search_body(
    conn,
    search_run_id: int,
    request: SearchRequest,
) -> SearchSummary:

    from literature_monitor.core.review_service import resolve_topic, score_groups

    resolve_topic(request)
    issns = resolve_issns(request.journal_pool_ids, request.journal_issns)
    records: list[ProviderPaper] = []
    errors: list[str] = []

    fetchers = [
        ("crossref", lambda r=request, i=issns: fetch_crossref(r, i)),
        ("openalex", lambda r=request, i=issns: fetch_openalex(r, i)),
    ]
    if request.include_conferences:
        fetchers.append(
            ("crossref_conferences", lambda r=request: fetch_crossref_conferences(r))
        )

    for name, fetcher in fetchers:
        try:
            records.extend(fetcher())
        except Exception as exc:  # partial failure is a product requirement
            errors.append(f"{name}: {exc}")

    grouped = deduplicate_records(records)
    selected_issns = set(issns)
    scored = 0

    scored = score_groups(conn, search_run_id, request, grouped, selected_issns, errors)

    status = "partial_failed" if errors and records else "completed"
    if errors and not records:
        status = "failed"
    repo.update_search_run(
        conn,
        search_run_id,
        status=status,
        error_message="; ".join(errors) if errors else None,
        total_fetched=len(records),
        total_after_dedup=len(grouped),
        total_scored=scored,
        completed=True,
    )
    return SearchSummary(
        search_run_id=search_run_id,
        status=status,
        total_fetched=len(records),
        total_after_dedup=len(grouped),
        total_scored=scored,
        error_message="; ".join(errors) if errors else None,
    )


def merge_group(group: list[ProviderPaper]) -> Paper:
    papers = [item.paper for item in group]
    primary = sorted(
        papers,
        key=lambda paper: (
            0 if paper.abstract else 1,
            0 if paper.doi else 1,
            -(paper.citation_count or 0),
        ),
    )[0]
    return Paper(
        doi=first_value(papers, "doi"),
        title=primary.title,
        authors=primary.authors or first_value(papers, "authors") or [],
        journal=first_value(papers, "journal"),
        issn=first_value(papers, "issn"),
        publisher=first_value(papers, "publisher"),
        year=first_value(papers, "year"),
        publication_date=first_value(papers, "publication_date"),
        abstract=first_value(papers, "abstract"),
        url=first_value(papers, "url"),
        oa_url=first_value(papers, "oa_url"),
        citation_count=max((paper.citation_count or 0 for paper in papers), default=0),
        topics=merge_topics(papers),
    )


def first_value(papers: list[Paper], attr: str) -> Any:
    for paper in papers:
        value = getattr(paper, attr)
        if value:
            return value
    return None


def merge_topics(papers: list[Paper]) -> list[str]:
    topics = []
    for paper in papers:
        for topic in paper.topics:
            if topic not in topics:
                topics.append(topic)
    return topics[:20]


def request_from_dict(data: dict[str, Any]) -> SearchRequest:
    def list_field(name: str) -> list[str]:
        value = data.get(name, [])
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]

    return SearchRequest(
        date_from=str(data.get("date_from") or ""),
        date_to=str(data.get("date_to") or ""),
        topic_id=(str(data["topic_id"]).strip() if data.get("topic_id") else None),
        keywords=list_field("keywords"),
        synonyms=list_field("synonyms"),
        negative_keywords=list_field("negative_keywords"),
        journal_pool_ids=list_field("journal_pool_ids"),
        journal_issns=list_field("journal_issns"),
        include_conferences=bool(data.get("include_conferences", False)),
        min_score=float(data.get("min_score") or 0),
        max_results_per_source=int(data.get("max_results_per_source") or 10000),
    )


def load_request_for_run(search_run: dict[str, Any]) -> SearchRequest:
    return request_from_dict(json.loads(search_run["query_json"]))
