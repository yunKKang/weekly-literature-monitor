"""Manual literature search orchestration."""

from __future__ import annotations

import json
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
from literature_monitor.core.scoring import score_for_request
from literature_monitor.db import repositories as repo
from literature_monitor.db.connection import connect
from literature_monitor.db.schema import init_db
from literature_monitor.providers.crossref import (
    fetch_crossref,
    fetch_crossref_conferences,
)
from literature_monitor.providers.openalex import fetch_openalex


def create_and_run_search(request: SearchRequest) -> SearchSummary:
    conn = connect()
    init_db(conn)
    search_run_id = repo.create_search_run(
        conn, asdict(request), request.date_from, request.date_to
    )
    return run_search(search_run_id, request)


def run_search(search_run_id: int, request: SearchRequest) -> SearchSummary:
    conn = connect()
    init_db(conn)
    repo.update_search_run(conn, search_run_id, status="running", started=True)

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

    for group in grouped:
        paper = merge_group(group)
        paper_id = repo.upsert_paper(conn, paper)
        for provider_record in group:
            repo.add_source_record(conn, paper_id, provider_record.source_record)
        score = score_for_request(conn, paper_id, paper, request, selected_issns)
        if score.total_score >= request.min_score:
            repo.add_score(conn, search_run_id, paper_id, score)
            scored += 1

    # Post-scoring: LLM review on MEDIUM+ papers
    llm_review_count = 0
    try:
        from literature_monitor.topic.loader import load_topic
        from literature_monitor.pipeline.stages.llm_review import LLMReviewStage
        from literature_monitor.pipeline.base import PipelineState

        # Try to load topic from request's journal_pool_ids
        topic = None
        for pool_id in request.journal_pool_ids:
            # Map pool IDs back to topic IDs
            for tid in ["gfcf_environment", "algal_bloom_ml"]:
                try:
                    t = load_topic(tid)
                    if any(pid in request.journal_pool_ids for pid in t.journal_pool_ids):
                        topic = t
                        break
                except Exception:
                    continue
            if topic:
                break

        if topic and topic.llm_review.enabled:
            llm_stage = LLMReviewStage(
                topic_name=topic.name,
                topic_description=topic.description,
                provider=topic.llm_review.provider,
                model=topic.llm_review.model,
                base_url=topic.llm_review.base_url or "",
                min_level="MEDIUM",
                max_papers=topic.llm_review.max_papers_per_run,
                max_cost_usd=topic.llm_review.max_cost_usd,
                prompt_template=topic.llm_review.prompt_template or None,
                concurrency=topic.llm_review.concurrency,
            )

            # Re-score MEDIUM+ papers through LLM
            from literature_monitor.db import repositories as repo2
            for group in grouped:
                paper = merge_group(group)
                paper_id = repo2.upsert_paper(conn, paper)
                # Check if this paper was scored MEDIUM+
                existing = conn.execute(
                    "SELECT total_score, relevance_level, score_breakdown_json "
                    "FROM scored_results WHERE search_run_id = ? AND paper_id = ?",
                    (search_run_id, paper_id),
                ).fetchone()
                if not existing:
                    continue
                if existing["relevance_level"] not in ("HIGH", "MEDIUM"):
                    continue

                # Build PipelineState for LLM review
                breakdown = json.loads(existing["score_breakdown_json"])
                state = PipelineState(
                    title=paper.title,
                    abstract=paper.abstract or "",
                    journal=paper.journal or "",
                    year=paper.year,
                    total_score=existing["total_score"],
                    relevance_level=existing["relevance_level"],
                    breakdown=breakdown,
                )
                result_state = llm_stage.run(state)

                # If LLM changed the score/level, update DB
                if result_state.total_score != existing["total_score"] or \
                   result_state.relevance_level != existing["relevance_level"]:
                    conn.execute(
                        "UPDATE scored_results SET total_score = ?, relevance_level = ?, "
                        "score_breakdown_json = ? WHERE search_run_id = ? AND paper_id = ?",
                        (
                            result_state.total_score,
                            result_state.relevance_level,
                            json.dumps(result_state.breakdown),
                            search_run_id,
                            paper_id,
                        ),
                    )
                    llm_review_count += 1
            conn.commit()
    except Exception as e:
        errors.append(f"llm_review: {e}")

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
    summary_msg = f"Scored {scored} papers"
    if llm_review_count:
        summary_msg += f", LLM reviewed {llm_review_count}"
    if errors:
        summary_msg += f", errors: {'; '.join(errors)}"

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
        keywords=list_field("keywords"),
        synonyms=list_field("synonyms"),
        negative_keywords=list_field("negative_keywords"),
        journal_pool_ids=list_field("journal_pool_ids"),
        journal_issns=list_field("journal_issns"),
        include_conferences=bool(data.get("include_conferences", False)),
        min_score=float(data.get("min_score") or 0),
        max_results_per_source=int(data.get("max_results_per_source") or 200),
    )


def load_request_for_run(search_run: dict[str, Any]) -> SearchRequest:
    return request_from_dict(json.loads(search_run["query_json"]))
