"""Async provider wrappers — concurrent fetching via asyncio.to_thread.

Wraps the synchronous Crossref and OpenAlex providers with async versions.
Uses asyncio.to_thread() to run blocking I/O in the thread pool, then
asyncio.gather() for true concurrency.

Future: replace with native httpx.AsyncClient when performance demands it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from literature_monitor.core.models import ProviderPaper, SearchRequest
from literature_monitor.compat import SRC  # noqa: F401  # ensures src/ on sys.path
from literature_monitor.providers.crossref import (
    fetch_crossref,
    fetch_crossref_conferences,
)
from literature_monitor.providers.openalex import fetch_openalex


async def async_fetch_crossref(
    request: SearchRequest, issns: list[str]
) -> list[ProviderPaper]:
    """Fetch from Crossref in a background thread."""
    return await asyncio.to_thread(fetch_crossref, request, issns)


async def async_fetch_openalex(
    request: SearchRequest, issns: list[str]
) -> list[ProviderPaper]:
    """Fetch from OpenAlex in a background thread."""
    return await asyncio.to_thread(fetch_openalex, request, issns)


async def async_fetch_conferences(
    request: SearchRequest,
) -> list[ProviderPaper]:
    """Fetch conference papers from Crossref in a background thread."""
    return await asyncio.to_thread(fetch_crossref_conferences, request)


async def fetch_all_providers(
    request: SearchRequest,
    issns: list[str],
    *,
    include_conferences: bool = False,
) -> tuple[list[ProviderPaper], list[str]]:
    """Fetch from all providers concurrently.

    Returns:
        (records, errors) — partial results on provider failure.
    """
    tasks = {
        "crossref": async_fetch_crossref(request, issns),
        "openalex": async_fetch_openalex(request, issns),
    }
    if include_conferences:
        tasks["crossref_conferences"] = async_fetch_conferences(request)

    # gather with return_exceptions to tolerate partial failure
    results = await asyncio.gather(
        *tasks.values(), return_exceptions=True
    )

    records: list[ProviderPaper] = []
    errors: list[str] = []
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            errors.append(f"{name}: {result}")
        else:
            records.extend(result)

    return records, errors
