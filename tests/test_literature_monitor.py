"""Tests for the productized literature_monitor package."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

from literature_monitor.core.dedup import (  # noqa: E402
    deduplicate_records,
    normalize_title,
    normalized_doi,
)
from literature_monitor.core.export import (  # noqa: E402
    export_bibtex,
    export_csv,
    export_markdown,
)
from literature_monitor.core.journals import resolve_issns  # noqa: E402
from literature_monitor.core.models import (  # noqa: E402
    Paper,
    PaperSourceRecord,
    ProviderPaper,
    ScoreResult,
    SearchRequest,
)
from literature_monitor.core.scoring import (  # noqa: E402
    collect_keyword_hits,
    negative_keyword_penalty,
    recency_bonus,
    relevance_level,
    should_apply_legacy_gfcf,
    snippet,
)
from literature_monitor.providers.openalex import (  # noqa: E402
    rebuild_inverted_abstract,
)

# ---------------------------------------------------------------------------
# OpenAlex abstract rebuild
# ---------------------------------------------------------------------------


class TestRebuildInvertedAbstract:
    def test_none_returns_none(self):
        assert rebuild_inverted_abstract(None) is None

    def test_empty_dict_returns_none(self):
        assert rebuild_inverted_abstract({}) is None

    def test_single_word(self):
        result = rebuild_inverted_abstract({"hello": [0]})
        assert result == "hello"

    def test_multi_word_ordering(self):
        inv = {"world": [1], "hello": [0]}
        result = rebuild_inverted_abstract(inv)
        assert result == "hello world"

    def test_multi_position_words(self):
        inv = {"the": [0, 3], "cat": [1], "sat": [2]}
        result = rebuild_inverted_abstract(inv)
        assert result == "the cat sat the"

    def test_complex_abstract(self):
        inv = {
            "capital": [0],
            "investment": [1],
            "drives": [2],
            "carbon": [3],
            "emissions": [4],
        }
        result = rebuild_inverted_abstract(inv)
        assert "capital" in result
        assert "investment" in result
        assert "carbon" in result


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDedup:
    def _paper(self, doi=None, title="Test Title", year="2026", authors=None):
        return ProviderPaper(
            paper=Paper(doi=doi, title=title, year=year, authors=authors or []),
            source_record=PaperSourceRecord(
                source="test", source_work_id=doi, doi=doi, raw={}
            ),
        )

    def test_same_doi_merges(self):
        a = self._paper(doi="10.1234/test")
        b = self._paper(doi="10.1234/test")
        groups = deduplicate_records([a, b])
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_different_doi_no_merge(self):
        a = self._paper(doi="10.1234/a")
        b = self._paper(doi="10.1234/b")
        groups = deduplicate_records([a, b])
        assert len(groups) == 2

    def test_no_doi_title_year_author_merges(self):
        kw = dict(
            doi=None,
            title="Investment and Carbon",
            year="2026",
            authors=["Doe, J."],
        )
        a = self._paper(**kw)
        b = self._paper(**kw)
        groups = deduplicate_records([a, b])
        assert len(groups) == 1

    def test_no_doi_different_title_no_merge(self):
        a = self._paper(doi=None, title="Alpha", year="2026")
        b = self._paper(doi=None, title="Beta", year="2026")
        groups = deduplicate_records([a, b])
        assert len(groups) == 2

    def test_mixed_doi_and_no_doi(self):
        a = self._paper(doi="10.1234/x")
        b = self._paper(doi=None, title="Something", year="2026")
        groups = deduplicate_records([a, b])
        assert len(groups) == 2

    def test_empty_records(self):
        assert deduplicate_records([]) == []


class TestNormalizeHelpers:
    def test_normalize_title_lowercase_strips(self):
        assert normalize_title("  Hello, World!  ") == "hello world"

    def test_normalize_title_none(self):
        assert normalize_title(None) == ""

    def test_normalized_doi_strips_url(self):
        assert normalized_doi("https://doi.org/10.1234/test") == "10.1234/test"

    def test_normalized_doi_none(self):
        assert normalized_doi(None) is None


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


class TestScoring:
    def _paper(self, **kwargs):
        defaults = {
            "doi": "10.1234/test",
            "title": "Capital investment and carbon emissions",
            "abstract": "This paper examines investment in infrastructure.",
            "journal": "Test Journal",
            "topics": ["environment"],
        }
        defaults.update(kwargs)
        return Paper(**defaults)

    def test_collect_keyword_hits_title(self):
        paper = self._paper()
        request = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["capital"],
        )
        hits = collect_keyword_hits(paper, request)
        assert any(h.field == "title" and h.keyword == "capital" for h in hits)

    def test_collect_keyword_hits_abstract(self):
        paper = self._paper()
        request = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["infrastructure"],
        )
        hits = collect_keyword_hits(paper, request)
        assert any(h.field == "abstract" for h in hits)

    def test_collect_keyword_hits_no_match(self):
        paper = self._paper()
        request = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["quantum"],
        )
        hits = collect_keyword_hits(paper, request)
        assert len(hits) == 0

    def test_negative_keyword_penalty(self):
        paper = self._paper()
        penalty = negative_keyword_penalty(paper, ["carbon"])
        assert penalty == 20.0

    def test_negative_keyword_penalty_no_match(self):
        paper = self._paper()
        penalty = negative_keyword_penalty(paper, ["quantum"])
        assert penalty == 0.0

    def test_negative_keyword_penalty_empty(self):
        paper = self._paper()
        assert negative_keyword_penalty(paper, []) == 0.0

    def test_recency_bonus_recent(self):
        bonus = recency_bonus("2026-05-01")
        assert bonus > 5.0

    def test_recency_bonus_old(self):
        bonus = recency_bonus("2020-01-01")
        assert bonus < 3.0

    def test_recency_bonus_none(self):
        assert recency_bonus(None) == 0.0

    def test_recency_bonus_year_only(self):
        bonus = recency_bonus("2026")
        assert bonus > 0.0

    def test_relevance_level_high(self):
        assert relevance_level(80, "LOW") == "HIGH"

    def test_relevance_level_legacy_high(self):
        assert relevance_level(10, "HIGH") == "HIGH"

    def test_relevance_level_medium(self):
        assert relevance_level(40, "LOW") == "MEDIUM"

    def test_relevance_level_low(self):
        assert relevance_level(5, "LOW") == "LOW"

    def test_snippet_found(self):
        result = snippet("The capital investment is key", "capital")
        assert "capital" in result.lower()

    def test_snippet_not_found(self):
        result = snippet("Short text here", "quantum")
        assert result == "Short text here"

    def test_legacy_gfcf_disabled_for_environment_pool(self):
        request = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            journal_pool_ids=["pool_environment_high_quality"],
        )
        assert should_apply_legacy_gfcf(request) is False

    def test_legacy_gfcf_enabled_for_legacy_pool(self):
        request = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            journal_pool_ids=["main_pool"],
        )
        assert should_apply_legacy_gfcf(request) is True


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport:
    def _results(self):
        return [
            {
                "title": "Test Paper",
                "journal": "Test Journal",
                "year": "2026",
                "publication_date": "2026-04-01",
                "doi": "10.1234/test",
                "url": "https://example.com",
                "total_score": 75.5,
                "relevance_level": "HIGH",
                "authors": ["Doe, Jane", "Smith, John"],
                "abstract": "An abstract about test.",
            }
        ]

    def test_export_csv_header(self):
        rows = self._results()
        csv = export_csv(rows)
        assert "title" in csv.split("\n")[0]
        assert "10.1234/test" in csv

    def test_export_bibtex_format(self):
        rows = self._results()
        bibtex = export_bibtex(rows)
        assert bibtex.startswith("@article{")
        assert "Doe" in bibtex
        assert "10.1234/test" in bibtex

    def test_export_markdown_format(self):
        rows = self._results()
        md = export_markdown(rows)
        assert "# Literature Search Results" in md
        assert "Test Paper" in md
        assert "HIGH" in md

    def test_export_empty(self):
        assert "title" in export_csv([])
        assert export_bibtex([]) == ""
        assert "Results" in export_markdown([])


# ---------------------------------------------------------------------------
# SearchRequest
# ---------------------------------------------------------------------------


class TestSearchRequest:
    def test_query_terms_dedup(self):
        req = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["carbon", "carbon"],
            synonyms=["co2", "carbon"],
        )
        assert req.query_terms == ["carbon", "co2"]

    def test_query_text(self):
        req = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["carbon", "emissions"],
        )
        assert req.query_text == "carbon emissions"

    def test_query_terms_empty(self):
        req = SearchRequest(date_from="2026-01-01", date_to="2026-12-31")
        assert req.query_terms == []
        assert req.query_text == ""

    def test_provider_query_text_uses_keywords_only(self):
        req = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["algal bloom machine learning"],
            synonyms=["cyanobacteria", "remote sensing"],
        )
        assert req.provider_query_text == "algal bloom machine learning"
        assert "cyanobacteria" in req.query_text


class TestJournalResolution:
    def test_default_scope_uses_configured_issns(self):
        issns = resolve_issns([], [])
        assert "1088-1980" in issns
        assert len(issns) > 50

    def test_environment_high_quality_pool_exists(self):
        from literature_monitor.core.journals import list_journal_pools

        pools = list_journal_pools()
        env_pool = next(
            pool for pool in pools if pool["id"] == "pool_environment_high_quality"
        )
        assert "Environmental" in env_pool["name"]
        assert len(env_pool["issns"]) >= 40
        assert "0013-936X" in env_pool["issns"]

    def test_algal_bloom_ml_pool_exists(self):
        from literature_monitor.core.journals import list_journal_pools

        pools = list_journal_pools()
        topic_pool = next(pool for pool in pools if pool["id"] == "pool_algal_bloom_ml")
        assert "Algal Blooms" in topic_pool["name"]
        assert len(topic_pool["issns"]) >= 80
        assert "1568-9883" in topic_pool["issns"]


class TestSearchTopics:
    def test_algal_bloom_ml_topic_exists(self):
        from literature_monitor.core.topics import list_search_topics

        topics = list_search_topics()
        topic = next(item for item in topics if item["id"] == "algal_bloom_ml")
        assert topic["journal_pool_ids"] == ["pool_algal_bloom_ml"]
        assert topic["date_from"] == "2020-01-01"
        assert any("harmful algal bloom" in kw for kw in topic["keywords"])
        assert "biofuel" in topic["negative_keywords"]


# ---------------------------------------------------------------------------
# Search service: partial failure handling
# ---------------------------------------------------------------------------


class TestSearchServicePartialFailure:
    def test_crossref_failure_preserves_openalex(self):
        """When one provider fails, the other's results are still used."""
        from literature_monitor.core.search_service import run_search
        from literature_monitor.db.connection import connect
        from literature_monitor.db.schema import init_db

        with tempfile.TemporaryDirectory() as tmp:
            import os
            os.environ["LITMON_DB_PATH"] = str(Path(tmp) / "test.sqlite3")
            try:
                conn = connect()
                init_db(conn)
                from literature_monitor.db import repositories as repo

                run_id = repo.create_search_run(
                    conn, {"test": True}, "2026-01-01", "2026-12-31"
                )

                fake_paper = ProviderPaper(
                    paper=Paper(doi="10.1234/x", title="Test Paper", year="2026"),
                    source_record=PaperSourceRecord(
                        source="openalex",
                        source_work_id="W1",
                        doi="10.1234/x",
                        raw={},
                    ),
                )

                with (
                    patch(
                        "literature_monitor.core.search_service.fetch_crossref",
                        side_effect=RuntimeError("network down"),
                    ),
                    patch(
                        "literature_monitor.core.search_service.fetch_openalex",
                        return_value=[fake_paper],
                    ),
                    patch(
                        "literature_monitor.core.search_service.resolve_issns",
                        return_value=["1234-5678"],
                    ),
                ):
                    summary = run_search(run_id, SearchRequest(
                        date_from="2026-01-01",
                        date_to="2026-12-31",
                        keywords=["test"],
                    ))

                assert summary.status == "partial_failed"
                assert summary.total_fetched == 1
                assert "crossref" in summary.error_message
            finally:
                del os.environ["LITMON_DB_PATH"]

    def test_include_conferences_fetches_conference_provider(self):
        """Conference fetch is opt-in and contributes records to the run."""
        from literature_monitor.core.search_service import run_search
        from literature_monitor.db.connection import connect
        from literature_monitor.db.schema import init_db

        with tempfile.TemporaryDirectory() as tmp:
            import os
            os.environ["LITMON_DB_PATH"] = str(Path(tmp) / "test.sqlite3")
            try:
                conn = connect()
                init_db(conn)
                from literature_monitor.db import repositories as repo

                run_id = repo.create_search_run(
                    conn, {"test": True}, "2026-01-01", "2026-12-31"
                )
                conference_paper = ProviderPaper(
                    paper=Paper(
                        doi="10.1234/conf",
                        title="Conference Carbon Paper",
                        year="2026",
                    ),
                    source_record=PaperSourceRecord(
                        source="crossref",
                        source_work_id="10.1234/conf",
                        doi="10.1234/conf",
                        raw={},
                    ),
                )

                with (
                    patch(
                        "literature_monitor.core.search_service.fetch_crossref",
                        return_value=[],
                    ),
                    patch(
                        "literature_monitor.core.search_service.fetch_openalex",
                        return_value=[],
                    ),
                    patch(
                        "literature_monitor.core.search_service.fetch_crossref_conferences",
                        return_value=[conference_paper],
                    ),
                    patch(
                        "literature_monitor.core.search_service.resolve_issns",
                        return_value=["1234-5678"],
                    ),
                ):
                    summary = run_search(run_id, SearchRequest(
                        date_from="2026-01-01",
                        date_to="2026-12-31",
                        keywords=["conference"],
                        include_conferences=True,
                    ))

                assert summary.status == "completed"
                assert summary.total_fetched == 1
            finally:
                del os.environ["LITMON_DB_PATH"]


# ---------------------------------------------------------------------------
# Database: repositories filtering
# ---------------------------------------------------------------------------


class TestRepositoryFiltering:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        import os
        os.environ["LITMON_DB_PATH"] = str(tmp_path / "test.sqlite3")
        from literature_monitor.db import repositories as repo
        from literature_monitor.db.connection import connect
        from literature_monitor.db.schema import init_db

        self.conn = connect()
        init_db(self.conn)
        self.repo = repo

        # Create a search run and some papers
        self.run_id = repo.create_search_run(
            self.conn, {"test": True}, "2026-01-01", "2026-12-31"
        )
        repo.update_search_run(
            self.conn,
            self.run_id,
            status="completed",
            total_scored=3,
            completed=True,
        )

        papers_data = [
            (
                "Alpha Paper", "10.1/a", "Journal A",
                "An abstract about alpha.", 80.0, "HIGH",
            ),
            (
                "Beta Paper", "10.1/b", "Journal B",
                "An abstract about beta.", 40.0, "MEDIUM",
            ),
            ("Gamma Paper", None, "Journal A", None, 10.0, "LOW"),
        ]

        for title, doi, journal, abstract, score, level in papers_data:
            paper = Paper(
                doi=doi,
                title=title,
                journal=journal,
                abstract=abstract,
                year="2026",
            )
            pid = repo.upsert_paper(self.conn, paper)
            sr = ScoreResult(
                total_score=score,
                relevance_level=level,
                rule_score=score,
                text_score=0,
                recency_score=0,
                journal_score=0,
                matched_pipelines=[],
                matched_keywords=[],
                keyword_hits=[],
                breakdown={},
            )
            repo.add_score(self.conn, self.run_id, pid, sr)

        yield
        del os.environ["LITMON_DB_PATH"]

    def test_list_all(self):
        total, rows = self.repo.list_results(self.conn, self.run_id)
        assert total == 3
        assert len(rows) == 3

    def test_filter_relevance_high(self):
        total, rows = self.repo.list_results(self.conn, self.run_id, relevance="HIGH")
        assert total == 1
        assert rows[0]["relevance_level"] == "HIGH"

    def test_filter_min_score(self):
        total, rows = self.repo.list_results(self.conn, self.run_id, min_score=30)
        assert total == 2

    def test_filter_journal(self):
        total, rows = self.repo.list_results(
            self.conn, self.run_id, journal="Journal A"
        )
        assert total == 2

    def test_filter_has_doi_true(self):
        total, rows = self.repo.list_results(self.conn, self.run_id, has_doi=True)
        assert total == 2

    def test_filter_has_doi_false(self):
        total, rows = self.repo.list_results(self.conn, self.run_id, has_doi=False)
        assert total == 1

    def test_filter_has_abstract_true(self):
        total, rows = self.repo.list_results(self.conn, self.run_id, has_abstract=True)
        assert total == 2

    def test_filter_has_abstract_false(self):
        total, rows = self.repo.list_results(self.conn, self.run_id, has_abstract=False)
        assert total == 1

    def test_filter_year(self):
        total, rows = self.repo.list_results(self.conn, self.run_id, year="2026")
        assert total == 3
        assert all(row["year"] == "2026" for row in rows)

    def test_filter_date_range(self):
        paper = Paper(
            doi="10.1/date",
            title="Date Paper",
            journal="Journal C",
            abstract="An abstract.",
            year="2025",
            publication_date="2025-06-01",
        )
        pid = self.repo.upsert_paper(self.conn, paper)
        sr = ScoreResult(
            total_score=60.0,
            relevance_level="HIGH",
            rule_score=60,
            text_score=0,
            recency_score=0,
            journal_score=0,
            matched_pipelines=[],
            matched_keywords=[],
            keyword_hits=[],
            breakdown={},
        )
        self.repo.add_score(self.conn, self.run_id, pid, sr)

        total, rows = self.repo.list_results(
            self.conn,
            self.run_id,
            date_from="2025-01-01",
            date_to="2025-12-31",
        )
        assert total == 1
        assert rows[0]["title"] == "Date Paper"

    def test_sort_by_score_desc(self):
        _, rows = self.repo.list_results(self.conn, self.run_id, sort="score")
        scores = [r["total_score"] for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_sort_by_title_asc(self):
        _, rows = self.repo.list_results(self.conn, self.run_id, sort="title")
        titles = [r["title"] for r in rows]
        assert titles == sorted(titles)

    def test_pagination(self):
        _, page1 = self.repo.list_results(self.conn, self.run_id, limit=2, offset=0)
        _, page2 = self.repo.list_results(self.conn, self.run_id, limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 1
        assert page1[0]["id"] != page2[0]["id"]

    def test_get_result_detail(self):
        _, rows = self.repo.list_results(self.conn, self.run_id, limit=1)
        paper_id = rows[0]["id"]
        detail = self.repo.get_result_detail(self.conn, self.run_id, paper_id)
        assert detail is not None
        assert "keyword_hits" in detail
        assert "source_records" in detail

    def test_get_result_detail_not_found(self):
        assert self.repo.get_result_detail(self.conn, self.run_id, 9999) is None


# ---------------------------------------------------------------------------
# OpenAlex work parsing
# ---------------------------------------------------------------------------


class TestOpenAlexParseWork:
    def test_parse_basic_work(self):
        from literature_monitor.providers.openalex import parse_openalex_work

        item = {
            "id": "https://openalex.org/W123",
            "title": "Carbon Emissions and Investment",
            "doi": "https://doi.org/10.1234/test",
            "publication_year": 2026,
            "publication_date": "2026-03-15",
            "authorships": [
                {"author": {"display_name": "Jane Doe"}},
                {"author": {"display_name": "John Smith"}},
            ],
            "primary_location": {
                "source": {
                    "display_name": "Test Journal",
                    "issn": ["1234-5678"],
                    "host_organization_name": "Test Publisher",
                },
                "landing_page_url": "https://example.com/paper",
                "pdf_url": "https://example.com/paper.pdf",
            },
            "open_access": {"oa_url": "https://example.com/oa"},
            "abstract_inverted_index": {"Carbon": [0], "emissions": [1], "matter": [2]},
            "cited_by_count": 42,
            "concepts": [{"display_name": "Carbon"}],
            "topics": [{"display_name": "Environmental Science"}],
        }
        result = parse_openalex_work(item)
        assert result is not None
        assert result.paper.title == "Carbon Emissions and Investment"
        assert result.paper.doi == "10.1234/test"
        assert result.paper.year == "2026"
        assert result.paper.citation_count == 42
        assert result.paper.abstract == "Carbon emissions matter"
        assert result.source_record.source == "openalex"
        assert len(result.paper.authors) == 2
        assert result.paper.issn == "1234-5678"

    def test_parse_work_no_title_returns_none(self):
        from literature_monitor.providers.openalex import parse_openalex_work

        item = {"id": "https://openalex.org/W456", "title": None, "display_name": None}
        assert parse_openalex_work(item) is None

    def test_parse_work_topics_deduped(self):
        from literature_monitor.providers.openalex import parse_openalex_work

        item = {
            "id": "https://openalex.org/W789",
            "title": "Test Paper",
            "publication_year": 2025,
            "authorships": [],
            "concepts": [{"display_name": "Concept A"}, {"display_name": "Concept B"}],
            "topics": [{"display_name": "Topic A"}, {"display_name": "Concept A"}],
        }
        result = parse_openalex_work(item)
        assert result is not None
        assert result.paper.topics.count("Concept A") == 1

    def test_chunked_splits_openalex_issn_filters(self):
        from literature_monitor.providers.openalex import chunked

        values = [str(index) for index in range(121)]
        chunks = chunked(values, 50)
        assert [len(chunk) for chunk in chunks] == [50, 50, 21]


class TestCrossrefProvider:
    def test_with_issns_uses_issn_fetch(self):
        """With ISSNs present, always fetch by ISSN (not keyword search)."""
        from literature_monitor.providers.crossref import fetch_crossref

        request = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["carbon"],
            max_results_per_source=10,
        )

        with patch(
            "literature_monitor.providers.crossref.fetch_recent_papers"
        ) as fetch:
            fetch.return_value = []
            fetch_crossref(request, ["1234-5678"])

        # Should use ISSN-based fetch, not keyword search
        fetch.assert_called_once()
        assert fetch.call_args.kwargs["issns"] == ["1234-5678"]
        assert fetch.call_args.kwargs["from_date"] == "2026-01-01"

    def test_without_issns_uses_keyword_search(self):
        """Without ISSNs, use keyword search API."""
        from literature_monitor.providers.crossref import fetch_crossref

        request = SearchRequest(
            date_from="2026-01-01",
            date_to="2026-12-31",
            keywords=["carbon"],
            max_results_per_source=10,
        )

        class Page:
            results = []

        with patch(
            "literature_monitor.providers.crossref.search_crossref_page"
        ) as search:
            search.return_value = Page()
            fetch_crossref(request, [])

        assert search.call_args.args[0].query == "carbon"


# ---------------------------------------------------------------------------
# Search service: merge_group and request_from_dict
# ---------------------------------------------------------------------------


class TestSearchServiceHelpers:
    def _provider(
        self,
        doi="10.1234/test",
        title="Paper",
        abstract=None,
        citation_count=0,
        topics=None,
        journal="J",
        issn="1234-5678",
    ):
        return ProviderPaper(
            paper=Paper(
                doi=doi,
                title=title,
                abstract=abstract,
                citation_count=citation_count,
                topics=topics or [],
                journal=journal,
                issn=issn,
                year="2026",
            ),
            source_record=PaperSourceRecord(
                source="test",
                source_work_id=doi,
                doi=doi,
                raw={},
            ),
        )

    def test_merge_prefers_abstract(self):
        from literature_monitor.core.search_service import merge_group
        p1 = self._provider(abstract=None)
        p2 = self._provider(abstract="Has abstract")
        merged = merge_group([p1, p2])
        assert merged.abstract == "Has abstract"

    def test_merge_prefers_doi(self):
        from literature_monitor.core.search_service import merge_group
        p1 = self._provider(doi=None, title="Paper")
        p2 = self._provider(doi="10.1234/test", title="Paper")
        merged = merge_group([p1, p2])
        assert merged.doi == "10.1234/test"

    def test_merge_max_citation_count(self):
        from literature_monitor.core.search_service import merge_group
        p1 = self._provider(citation_count=5)
        p2 = self._provider(citation_count=42)
        merged = merge_group([p1, p2])
        assert merged.citation_count == 42

    def test_merge_topics_deduped(self):
        from literature_monitor.core.search_service import merge_group
        p1 = self._provider(topics=["A", "B"])
        p2 = self._provider(topics=["B", "C"])
        merged = merge_group([p1, p2])
        assert merged.topics == ["A", "B", "C"]

    def test_request_from_dict_string_keywords(self):
        from literature_monitor.core.search_service import request_from_dict
        req = request_from_dict({
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
            "keywords": "carbon, emissions",
        })
        assert req.keywords == ["carbon", "emissions"]

    def test_request_from_dict_list_keywords(self):
        from literature_monitor.core.search_service import request_from_dict
        req = request_from_dict({
            "date_from": "2026-01-01",
            "date_to": "2026-02-01",
            "keywords": ["carbon"],
        })
        assert req.keywords == ["carbon"]

    def test_request_from_dict_defaults(self):
        from literature_monitor.core.search_service import request_from_dict
        req = request_from_dict({"date_from": "2026-01-01", "date_to": "2026-02-01"})
        assert req.min_score == 0
        assert req.max_results_per_source == 200


# ---------------------------------------------------------------------------
# Repository: source filter and upsert dedup
# ---------------------------------------------------------------------------


class TestRepositoryExtras:
    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        import os
        os.environ["LITMON_DB_PATH"] = str(tmp_path / "test.sqlite3")
        from literature_monitor.db import repositories as repo
        from literature_monitor.db.connection import connect
        from literature_monitor.db.schema import init_db

        self.conn = connect()
        init_db(self.conn)
        self.repo = repo
        self.run_id = repo.create_search_run(self.conn, {}, "2026-01-01", "2026-12-31")
        yield
        del os.environ["LITMON_DB_PATH"]

    def _add_paper_with_score(
        self, doi="10.1/a", title="Paper", journal="J", abstract="Abs"
    ):
        paper = Paper(
            doi=doi, title=title, journal=journal,
            abstract=abstract, year="2026",
        )
        pid = self.repo.upsert_paper(self.conn, paper)
        sr = ScoreResult(
            total_score=50.0, relevance_level="HIGH",
            rule_score=20, text_score=10, recency_score=10,
            journal_score=10, matched_pipelines=[],
            matched_keywords=[], keyword_hits=[], breakdown={},
        )
        self.repo.add_score(self.conn, self.run_id, pid, sr)
        return pid

    def test_source_filter(self):
        pid = self._add_paper_with_score()
        self.repo.add_source_record(self.conn, pid,
            PaperSourceRecord(source="openalex", source_work_id="W1", doi=None, raw={}))

        total, _ = self.repo.list_results(self.conn, self.run_id, source="openalex")
        assert total == 1
        total, _ = self.repo.list_results(self.conn, self.run_id, source="crossref")
        assert total == 0

    def test_upsert_by_doi_updates(self):
        p1 = Paper(doi="10.1234/x", title="Original", year="2026")
        pid1 = self.repo.upsert_paper(self.conn, p1)
        p2 = Paper(doi="10.1234/x", title="Updated Title", year="2026", abstract="New")
        pid2 = self.repo.upsert_paper(self.conn, p2)
        assert pid1 == pid2

    def test_upsert_by_title_year(self):
        p1 = Paper(doi=None, title="Unique Title", year="2026")
        pid1 = self.repo.upsert_paper(self.conn, p1)
        p2 = Paper(doi=None, title="Unique Title", year="2026", abstract="Later")
        pid2 = self.repo.upsert_paper(self.conn, p2)
        assert pid1 == pid2

    def test_upsert_keeps_title_and_normalized_title_in_sync(self):
        p1 = Paper(doi="10.1234/sync", title="A Longer Stable Title", year="2026")
        pid = self.repo.upsert_paper(self.conn, p1)
        p2 = Paper(doi="10.1234/sync", title="Short", year="2026")
        self.repo.upsert_paper(self.conn, p2)
        row = self.conn.execute(
            "SELECT title, normalized_title FROM papers WHERE id = ?", (pid,)
        ).fetchone()
        assert row["title"] == "A Longer Stable Title"
        assert row["normalized_title"] == "a longer stable title"


# ---------------------------------------------------------------------------
# FastAPI app smoke test
# ---------------------------------------------------------------------------


class TestFastAPISmoke:
    @pytest.fixture(autouse=True)
    def setup_app(self, tmp_path):
        import os
        os.environ["LITMON_DB_PATH"] = str(tmp_path / "test.sqlite3")
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi[testclient] not installed")
        from literature_monitor.api.app import create_app
        self.client = TestClient(create_app())
        yield
        del os.environ["LITMON_DB_PATH"]

    def test_health(self):
        r = self.client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_journal_pools(self):
        r = self.client.get("/api/journal-pools")
        assert r.status_code == 200
        data = r.json()
        assert "pools" in data
        assert len(data["pools"]) > 0

    def test_search_topics(self):
        r = self.client.get("/api/search-topics")
        assert r.status_code == 200
        data = r.json()
        assert any(topic["id"] == "algal_bloom_ml" for topic in data["topics"])

    def test_index_page(self):
        r = self.client.get("/")
        assert r.status_code == 200
        assert "Literature Search" in r.text

    def test_search_run_not_found(self):
        r = self.client.get("/api/search-runs/9999")
        assert r.status_code == 404
        assert r.json()["detail"] == "not_found"

    def test_results_not_found(self):
        r = self.client.get("/api/search-runs/9999/results")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_detail_not_found(self):
        r = self.client.get("/api/search-runs/9999/results/9999")
        assert r.status_code == 404
        assert r.json()["detail"] == "not_found"

    def test_export_bad_format(self):
        r = self.client.get("/api/search-runs/9999/export?format=xml")
        assert r.status_code == 400
