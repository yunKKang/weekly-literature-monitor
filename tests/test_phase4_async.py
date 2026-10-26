"""Tests for Phase 4: Async provider fetching and async search service."""

from __future__ import annotations

import pytest

from literature_monitor.core.models import (
    Paper,
    PaperSourceRecord,
    ProviderPaper,
    SearchRequest,
)


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
    @pytest.mark.asyncio
    async def test_partial_failure_tolerance(self, monkeypatch):
        from literature_monitor.providers import async_fetch

        record = ProviderPaper(
            Paper("10.1234/a", "Title"), PaperSourceRecord("test", "a", "10.1234/a", {})
        )

        async def failed(*args):
            raise RuntimeError("provider unavailable")

        async def success(*args):
            return [record]

        monkeypatch.setattr(async_fetch, "async_fetch_crossref", failed)
        monkeypatch.setattr(async_fetch, "async_fetch_openalex", success)
        records, errors = await async_fetch.fetch_all_providers(_make_request(), [])
        assert records == [record]
        assert errors == ["crossref: provider unavailable"]

    @pytest.mark.asyncio
    async def test_include_conferences_flag(self, monkeypatch):
        from unittest.mock import AsyncMock
        from literature_monitor.providers import async_fetch

        crossref, openalex, conferences = [AsyncMock(return_value=[]) for _ in range(3)]
        monkeypatch.setattr(async_fetch, "async_fetch_crossref", crossref)
        monkeypatch.setattr(async_fetch, "async_fetch_openalex", openalex)
        monkeypatch.setattr(async_fetch, "async_fetch_conferences", conferences)
        assert await async_fetch.fetch_all_providers(
            _make_request(), [], include_conferences=False
        ) == ([], [])
        conferences.assert_not_called()
        await async_fetch.fetch_all_providers(
            _make_request(), [], include_conferences=True
        )
        conferences.assert_awaited_once()
        assert crossref.await_count == openalex.await_count == 2


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

    @pytest.mark.asyncio
    async def test_async_create_and_run_search_uses_thread_local_db_connections(
        self, monkeypatch, tmp_path
    ):
        """Async search should not pass sqlite connections across threads."""
        from literature_monitor.core import async_search_service

        async def fake_fetch_all_providers(*args, **kwargs):
            return [], []

        monkeypatch.setenv("LITMON_DB_PATH", str(tmp_path / "litmon.sqlite3"))
        monkeypatch.setattr(
            async_search_service,
            "fetch_all_providers",
            fake_fetch_all_providers,
        )

        summary = await async_search_service.async_create_and_run_search(
            _make_request()
        )

        assert summary.status == "completed"
        assert summary.total_fetched == 0
        assert summary.total_after_dedup == 0
        assert summary.total_scored == 0
