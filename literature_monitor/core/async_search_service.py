"""Async search orchestration — concurrent provider fetching + async DB.

Drop-in async replacement for create_and_run_search/run_search.
Uses asyncio.gather for concurrent Crossref + OpenAlex fetching.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

from literature_monitor.compat import SRC  # noqa: F401
from literature_monitor.core.dedup import deduplicate_records
from literature_monitor.core.journals import resolve_issns
from literature_monitor.core.models import (
    Paper,
    ProviderPaper,
    SearchRequest,
    SearchSummary,
)
from literature_monitor.core.scoring import score_for_request
from literature_monitor.db import repositories as repo
from literature_monitor.db.async_connection import async_connect
from literature_monitor.db.connection import connect
from literature_monitor.db.schema import init_db
from literature_monitor.providers.async_fetch import fetch_all_providers

# Re-use merge_group from sync service
from literature_monitor.core.search_service import merge_group


async def async_create_and_run_search(request: SearchRequest) -> SearchSummary:
    """Async entry point — creates a search run and executes it."""
    conn = connect()
    init_db(conn)
    search_run_id = repo.create_search_run(
        conn, asdict(request), request.date_from, request.date_to
    )
    conn.close()
    return await async_run_search(search_run_id, request)


async def async_run_search(
    search_run_id: int, request: SearchRequest
) -> SearchSummary:
    """Async search execution with concurrent provider fetching."""
    conn = connect()
    init_db(conn)
    repo.update_search_run(conn, search_run_id, status="running", started=True)

    issns = resolve_issns(request.journal_pool_ids, request.journal_issns)

    # Concurrent fetching from all providers
    records, errors = await fetch_all_providers(
        request,
        issns,
        include_conferences=request.include_conferences,
    )

    grouped = deduplicate_records(records)
    selected_issns = set(issns)
    scored = 0

    for group in grouped:
        paper = merge_group(group)
        paper_id = repo.upsert_paper(conn, paper)
        for provider_record in group:
            repo.add_source_record(conn, paper_id, provider_record.source_record)
        score = score_for_request(conn, paper_id, paper, request, selected_issns)
        if score.total_score >= request.min_score:
            repo.add_score(conn, search_run_id, paper_id, score)
            scored += 1

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
    conn.close()
    return SearchSummary(
        search_run_id=search_run_id,
        status=status,
        total_fetched=len(records),
        total_after_dedup=len(grouped),
        total_scored=scored,
        error_message="; ".join(errors) if errors else None,
    )
