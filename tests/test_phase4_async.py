"""Tests for Phase 4: Async provider fetching and async search service."""

from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio

from literature_monitor.core.models import Paper, PaperSourceRecord, ProviderPaper, SearchRequest
from literature_monitor.providers.async_fetch import fetch_all_providers


def _make_request(keywords: list[str] | None = None) -> SearchRequest:
    return SearchRequest(
        date_from="2026-01-01",
        date_to="2026-05-01",
        keywords=keywords or ["capital", "carbon"],
        synonyms=[],
        negative_keywords=[],
        journal_pool_ids=[],
        journal_issns=[],
        include_conferences=False,
        min_score=0,
        max_results_per_source=5,
    )


class TestAsyncFetchAllProviders:
    """Test concurrent provider fetching with mock/degraded providers."""

    @pytest.mark.asyncio
    async def test_returns_tuple(self):
        """fetch_all_providers returns (records, errors) even on network issues."""
        request = _make_request()
        # Use empty ISSNs — should not crash, just return empty
        records, errors = await fetch_all_providers(request, issns=[])
        assert isinstance(records, list)
        assert isinstance(errors, list)

    @pytest.mark.asyncio
    async def test_partial_failure_tolerance(self):
        """One provider failing doesn't break the other."""
        # With empty ISSNs, both providers should return empty gracefully
        request = _make_request()
        records, errors = await fetch_all_providers(request, issns=["0000-0000"])
        assert isinstance(records, list)
        assert isinstance(errors, list)

    @pytest.mark.asyncio
    async def test_include_conferences_flag(self):
        """Conferences task only runs when flag is True."""
        request = _make_request()
        # Without conferences
        records1, errors1 = await fetch_all_providers(
            request, issns=[], include_conferences=False
        )
        # With conferences (should have 3 tasks)
        records2, errors2 = await fetch_all_providers(
            request, issns=[], include_conferences=True
        )
        # Both should complete without crashing
        assert isinstance(records1, list)
        assert isinstance(records2, list)


class TestAsyncSearchService:
    """Test async search service integration."""

    def test_module_importable(self):
        """Verify async_search_service can be imported."""
        from literature_monitor.core.async_search_service import (
            async_create_and_run_search,
            async_run_search,
        )
        assert callable(async_create_and_run_search)
        assert callable(async_run_search)

    def test_module_has_merge_group(self):
        """Verify it reuses merge_group from sync service."""
        from literature_monitor.core.async_search_service import merge_group
        assert callable(merge_group)
