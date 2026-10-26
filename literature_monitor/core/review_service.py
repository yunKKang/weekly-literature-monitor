"""One scoring and AI-review path for scheduled, synchronous and async searches."""

from __future__ import annotations

from literature_monitor.core.review_cache import ReviewCache
from literature_monitor.pipeline.engine import build_pipeline, run_pipeline
from literature_monitor.topic.loader import load_topic


def resolve_topic(request):
    if request.topic_id:
        try:
            return load_topic(request.topic_id)
        except FileNotFoundError as exc:
            raise ValueError(f"Unknown topic_id: {request.topic_id}") from exc
    if (
        "main_pool" in request.journal_pool_ids
        or "supplementary_pool" in request.journal_pool_ids
    ):
        return load_topic("gfcf_environment")
    return None


def review_papers(
    papers, request, *, conn=None, selected_issns=None, cache=None, include_ai=True
):
    topic = resolve_topic(request)
    cache = cache if cache is not None else ReviewCache(conn=conn)
    pipeline = build_pipeline(
        topic=topic,
        conn=conn,
        query_text=request.query_text,
        selected_issns=set(selected_issns or []),
        use_legacy=bool(topic),
        query_terms=[] if topic else request.query_terms,
        request_negative_keywords=request.negative_keywords,
        journal_pool_ids=request.journal_pool_ids
        or (topic.journal_pool_ids if topic else []),
        review_cache=cache,
        include_ai=include_ai,
    )
    # Review high-rule-score candidates first; one shared stage owns the budget.
    from literature_monitor.core.relevance_filter import (
        load_keyword_config,
        score_paper,
    )

    config = load_keyword_config() if topic else None
    ordered = (
        sorted(papers, key=lambda p: -score_paper(p.title, p.abstract, config).score)
        if topic
        else papers
    )
    return [(paper, *run_pipeline(pipeline, paper)) for paper in ordered]


def score_groups(conn, search_run_id, request, grouped, selected_issns, errors):
    from literature_monitor.core.search_service import merge_group
    from literature_monitor.db import repositories as repo

    papers = []
    for group in grouped:
        paper = merge_group(group)
        paper_id = repo.upsert_paper(conn, paper)
        for record in group:
            repo.add_source_record(conn, paper_id, record.source_record)
        papers.append(paper)
    scored, pending = 0, 0
    for paper, state, score in review_papers(
        papers, request, conn=conn, selected_issns=selected_issns
    ):
        paper_id = repo.upsert_paper(conn, paper)
        audit = state.breakdown.get("llm_review")
        if audit and audit.get("status") != "completed":
            pending += 1
        if score.total_score >= request.min_score:
            repo.add_score(conn, search_run_id, paper_id, score)
            scored += 1
    if pending:
        errors.append(f"ai_pending:{pending}")
    conn.commit()
    return scored
