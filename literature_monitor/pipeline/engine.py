"""Pipeline engine — build and run scoring pipelines from Topic config.

This is the main entry point for the new scoring system. It replaces
the monolithic score_for_request() with a Pipeline of independently
testable stages.

Usage:
    from literature_monitor.pipeline.engine import build_pipeline, run_pipeline

    pipeline = build_pipeline(topic, conn=conn, query_text="capital", selected_issns={...})
    state = run_pipeline(pipeline, paper, paper_id=42)
"""

from __future__ import annotations

import sqlite3

from literature_monitor.core.models import Paper, ScoreResult, KeywordHit
from literature_monitor.pipeline.base import Pipeline, PipelineState, Stage
from literature_monitor.pipeline.stages.hard_threshold import HardThresholdStage
from literature_monitor.pipeline.stages.rule_scoring import RuleScoringStage
from literature_monitor.pipeline.stages.text_relevance import TextRelevanceStage
from literature_monitor.pipeline.stages.recency_boost import RecencyBoostStage
from literature_monitor.pipeline.stages.journal_boost import JournalBoostStage
from literature_monitor.pipeline.stages.keyword_match import KeywordMatchStage
from literature_monitor.pipeline.stages.negative_filter import NegativeFilterStage
from literature_monitor.pipeline.stages.final_scoring import FinalScoringStage
from literature_monitor.pipeline.stages.llm_review import LLMReviewStage
from literature_monitor.topic.schema import Topic


def build_pipeline(
    topic: Topic | None = None,
    conn: sqlite3.Connection | None = None,
    query_text: str = "",
    selected_issns: set[str] | None = None,
    *,
    use_legacy: bool = True,
    query_terms: list[str] | None = None,
    request_negative_keywords: list[str] | None = None,
    journal_pool_ids: list[str] | None = None,
) -> Pipeline:
    """Build a scoring Pipeline from a Topic configuration.

    If topic is None, builds a legacy-compatible pipeline (backward compat).
    """
    stages: list[Stage] = []

    # 1. Hard threshold — reject papers that fail ALL pipelines' AND conditions
    if topic:
        # Build keyword set lookup from topic
        ks_map = {ks.name: ks.keywords for ks in topic.keyword_sets}
        # Build pipeline requirements from topic pipelines
        pipe_reqs = [pipe.hard_threshold.required_sets for pipe in topic.pipelines]
        stages.append(HardThresholdStage(
            keyword_sets=ks_map,
            pipeline_requirements=pipe_reqs,
        ))
    else:
        # No topic — pass through (legacy compat)
        stages.append(HardThresholdStage())

    # 2. Legacy rule scoring (Branch by Abstraction)
    if use_legacy:
        stages.append(RuleScoringStage(journal_pool_ids=journal_pool_ids))

    # 2b. Dynamic keyword matching (user query terms)
    if query_terms:
        stages.append(KeywordMatchStage(query_terms))

    # 3. Negative keyword filter
    all_negatives: list[str] = []
    if topic:
        for pipe in topic.pipelines:
            all_negatives.extend(pipe.negative_keywords)
        # Also from keyword_sets
        for ks in topic.keyword_sets:
            all_negatives.extend(ks.negative_keywords)
    # Merge in request-level negative keywords
    all_negatives.extend(request_negative_keywords or [])
    all_negatives = list(set(all_negatives))
    if all_negatives:
        penalty = topic.scoring.negative_penalty if topic else 20
        stages.append(NegativeFilterStage(all_negatives, penalty))

    # 4. Text relevance (FTS5)
    if conn and query_text:
        stages.append(TextRelevanceStage(conn, query_text))

    # 5. Recency boost
    stages.append(RecencyBoostStage())

    # 6. Journal boost
    if selected_issns:
        stages.append(JournalBoostStage(selected_issns))

    # 6b. LLM review (optional)
    if topic and topic.llm_review.enabled:
        stages.append(LLMReviewStage(
            topic_name=topic.name,
            topic_description=topic.description,
            provider=topic.llm_review.provider,
            model=topic.llm_review.model,
            min_level="MEDIUM",  # only review MEDIUM and above
            max_papers=topic.llm_review.max_papers_per_run,
            max_cost_usd=topic.llm_review.max_cost_usd,
            prompt_template=topic.llm_review.prompt_template or None,
            concurrency=topic.llm_review.concurrency,
        ))

    # 7. Final scoring
    scoring = topic.scoring if topic else None
    stages.append(FinalScoringStage(
        rule_weight=1.0,
        text_weight=0.6,
        legacy_high_threshold=scoring.high_threshold if scoring else 20,
        legacy_medium_threshold=scoring.medium_threshold if scoring else 12,
    ))

    return Pipeline(stages)


def paper_to_state(paper: Paper, paper_id: int = 0) -> PipelineState:
    """Convert a Paper model into a PipelineState for scoring."""
    return PipelineState(
        title=paper.title,
        abstract=paper.abstract or "",
        journal=paper.journal or "",
        topics=tuple(paper.topics),
        doi=paper.doi or "",
        year=paper.year,
        publication_date=paper.publication_date,
        issn=paper.issn,
        paper_id=paper_id,
    )


def state_to_score_result(state: PipelineState) -> ScoreResult:
    """Convert a PipelineState into the legacy ScoreResult model."""
    return ScoreResult(
        total_score=state.total_score,
        relevance_level=state.relevance_level,
        rule_score=state.rule_score,
        text_score=state.text_score,
        recency_score=state.recency_score,
        journal_score=state.journal_score,
        matched_pipelines=list(state.matched_pipelines),
        matched_keywords=list(state.matched_keywords),
        keyword_hits=list(state.keyword_hits),
        breakdown=dict(state.breakdown),
    )


def run_pipeline(
    pipeline: Pipeline,
    paper: Paper,
    paper_id: int = 0,
) -> tuple[PipelineState, ScoreResult]:
    """Run a pipeline on a paper and return both state and legacy result."""
    state = paper_to_state(paper, paper_id)
    state = pipeline.run(state)
    result = state_to_score_result(state)
    return state, result
