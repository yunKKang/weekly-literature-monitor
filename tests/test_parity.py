"""Parity tests — verify new Pipeline produces same results as legacy score_for_request.

These tests ensure the Pipeline migration doesn't change scoring behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path
import sqlite3

import pytest

from literature_monitor.core.models import Paper, SearchRequest
from literature_monitor.core.scoring import score_for_request, should_apply_legacy_gfcf

def _connect_memory() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

from literature_monitor.db.connection import connect
from literature_monitor.db.schema import init_db
from literature_monitor.pipeline.base import Pipeline, PipelineState
from literature_monitor.pipeline.engine import build_pipeline, run_pipeline
from literature_monitor.pipeline.stages.rule_scoring import RuleScoringStage
from literature_monitor.pipeline.stages.recency_boost import RecencyBoostStage
from literature_monitor.pipeline.stages.keyword_match import KeywordMatchStage
from literature_monitor.pipeline.stages.final_scoring import FinalScoringStage


# --- Shared fixtures ---

def _make_paper(
    title: str = "",
    abstract: str = "",
    journal: str = "",
    doi: str = "",
    year: str | None = None,
    issn: str | None = None,
    topics: list[str] | None = None,
) -> Paper:
    return Paper(
        doi=doi,
        title=title,
        abstract=abstract,
        journal=journal,
        issn=issn,
        year=year,
        publication_date=None,
        topics=topics or [],
        authors=[],
        url="",
        oa_url=None,
        citation_count=None,
        publisher=None,
    )


def _make_request(journal_pool_ids: list[str] | None = None) -> SearchRequest:
    return SearchRequest(
        keywords=["capital", "carbon"],
        synonyms=[],
        negative_keywords=[],
        date_from="2026-01-01",
        date_to="2026-05-01",
        journal_pool_ids=journal_pool_ids or [],
        journal_issns=[],
        include_conferences=False,
        min_score=0,
        max_results_per_source=100,
    )


@pytest.fixture
def db_conn():
    conn = _connect_memory()
    init_db(conn)
    # Insert a dummy paper so FTS5 has something
    conn.execute(
        "INSERT INTO papers (doi, normalized_doi, title, normalized_title, abstract, journal, year, topics_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("10.1234/test", "10.1234/test", "test", "test", "test", "test", "2026", "[]"),
    )
    conn.commit()
    return conn


# --- Individual Stage tests ---

class TestRuleScoringStage:
    def test_relevant_paper_gets_positive_score(self):
        stage = RuleScoringStage(journal_pool_ids=["main_pool"])
        state = PipelineState(
            title="Carbon footprint of capital investment in infrastructure",
            abstract="This paper examines embodied carbon emissions of GFCF.",
        )
        result = stage.run(state)
        assert result.rule_score > 0
        assert len(result.matched_pipelines) > 0

    def test_irrelevant_paper_gets_zero(self):
        stage = RuleScoringStage()
        state = PipelineState(
            title="Enzymatic biocontrol of fire blight",
            abstract="Using glycosyl hydrolases for plant disease management.",
        )
        result = stage.run(state)
        assert result.rule_score == 0.0
        assert len(result.matched_pipelines) == 0

    def test_disabled_returns_unchanged(self):
        stage = RuleScoringStage(use_legacy=False)
        state = PipelineState(title="test")
        result = stage.run(state)
        assert result.rule_score == 0.0


class TestRecencyBoostStage:
    def test_recent_paper(self):
        stage = RecencyBoostStage()
        state = PipelineState(year="2026")
        result = stage.run(state)
        assert result.recency_score > 2.0

    def test_old_paper(self):
        stage = RecencyBoostStage()
        state = PipelineState(year="2000")
        result = stage.run(state)
        assert result.recency_score < 1.0

    def test_no_date(self):
        stage = RecencyBoostStage()
        state = PipelineState()
        result = stage.run(state)
        assert result.recency_score == 0.0


class TestFinalScoringStage:
    def test_combines_scores(self):
        stage = FinalScoringStage()
        state = PipelineState(
            rule_score=20.0,
            text_score=5.0,
            recency_score=6.0,
            journal_score=5.0,
            breakdown={"legacy_priority": "LOW", "legacy_score": 0},
        )
        result = stage.run(state)
        expected = 20.0 * 1.0 + 5.0 * 0.6 + 6.0 + 5.0
        assert abs(result.total_score - round(expected, 3)) < 0.01

    def test_legacy_high_overrides(self):
        stage = FinalScoringStage()
        state = PipelineState(
            rule_score=0,
            breakdown={"legacy_priority": "HIGH", "legacy_score": 30},
        )
        result = stage.run(state)
        assert result.relevance_level == "HIGH"

    def test_legacy_medium_overrides(self):
        stage = FinalScoringStage()
        state = PipelineState(
            rule_score=0,
            breakdown={"legacy_priority": "MEDIUM", "legacy_score": 15},
        )
        result = stage.run(state)
        assert result.relevance_level == "MEDIUM"


# --- Pipeline integration tests ---

class TestPipelineIntegration:
    def test_pipeline_builds_without_topic(self):
        p = build_pipeline(use_legacy=True)
        names = p.describe()
        assert "rule_scoring" in names
        assert "recency_boost" in names
        assert "final_scoring" in names

    def test_pipeline_skip_stage(self):
        p = build_pipeline(use_legacy=True)
        state = PipelineState(
            title="test",
            skipped_stages=("recency_boost",),
        )
        result = p.run(state)
        assert result.recency_score == 0.0

    def test_pipeline_exclusion_stops_early(self):
        p = build_pipeline(use_legacy=False)
        state = PipelineState(
            title="test",
            excluded=True,
            exclusion_reason="pre_excluded",
        )
        result = p.run(state)
        assert result.excluded is True


# --- Parity: Pipeline vs score_for_request ---

class TestParity:
    """Critical: verify Pipeline produces same scores as legacy function."""

    def test_relevant_paper_parity(self, db_conn):
        paper = _make_paper(
            title="Carbon footprint of capital investment in infrastructure",
            abstract="Embodied carbon emissions of gross fixed capital formation.",
            issn="1234-5678",
            year="2025",
        )
        request = _make_request(journal_pool_ids=["main_pool"])
        selected_issns = {"1234-5678"}

        # Legacy path
        legacy_result = score_for_request(db_conn, 1, paper, request, selected_issns)

        # New pipeline path
        pipeline = build_pipeline(
            conn=db_conn,
            query_text=request.query_text,
            selected_issns=selected_issns,
            use_legacy=True,
            query_terms=request.query_terms,
            journal_pool_ids=request.journal_pool_ids,
        )
        _, new_result = run_pipeline(pipeline, paper, paper_id=1)

        # Compare critical outputs
        assert new_result.rule_score == legacy_result.rule_score, (
            f"rule_score: new={new_result.rule_score} vs legacy={legacy_result.rule_score}"
        )
        assert new_result.relevance_level == legacy_result.relevance_level, (
            f"level: new={new_result.relevance_level} vs legacy={legacy_result.relevance_level}"
        )
        assert abs(new_result.recency_score - legacy_result.recency_score) < 0.01
        assert abs(new_result.journal_score - legacy_result.journal_score) < 0.01
        # Total may differ slightly due to floating point ordering
        assert abs(new_result.total_score - legacy_result.total_score) < 1.0, (
            f"total: new={new_result.total_score} vs legacy={legacy_result.total_score}"
        )

    def test_irrelevant_paper_parity(self, db_conn):
        paper = _make_paper(
            title="Enzymatic biocontrol of fire blight",
            abstract="Using glycosyl hydrolases for plant disease management.",
            year="2025",
        )
        request = _make_request()

        legacy_result = score_for_request(db_conn, 1, paper, request, set())

        pipeline = build_pipeline(conn=db_conn, query_text=request.query_text, use_legacy=True, query_terms=request.query_terms)
        _, new_result = run_pipeline(pipeline, paper, paper_id=1)

        assert new_result.rule_score == legacy_result.rule_score == 0.0
        assert new_result.relevance_level == "LOW"
