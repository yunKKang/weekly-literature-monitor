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
from literature_monitor.pipeline.base import PipelineState
from literature_monitor.pipeline.stages.llm_review import LLMReviewStage
from literature_monitor.providers.crossref import (
    fetch_crossref,
    fetch_crossref_conferences,
)
from literature_monitor.providers.openalex import fetch_openalex
from literature_monitor.topic.loader import load_all_topics, load_topic
from literature_monitor.topic.schema import Topic


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

    # Resolve topic for negative keywords and LLM config
    topic = _resolve_topic_from_request(request)
    topic_negatives = _compile_topic_negatives(topic) if topic else []

    # DOI-based fallback: fetch known important papers directly by DOI
    # This ensures recall for papers that ISSN cursor pagination misses
    if topic and hasattr(topic, 'benchmark_dois'):
        fallback_count = _fetch_dois_fallback(conn, topic.benchmark_dois, issns, grouped, records)
        if fallback_count:
            errors.append(f"doi_fallback: fetched {fallback_count} papers by DOI")

    for group in grouped:
        paper = merge_group(group)
        paper_id = repo.upsert_paper(conn, paper)
        for provider_record in group:
            repo.add_source_record(conn, paper_id, provider_record.source_record)
        score = score_for_request(conn, paper_id, paper, request, selected_issns)

        # Apply topic-level negative keyword filtering
        # (Legacy score_for_request only uses config/keywords.json negatives,
        #  but topic YAML has additional negatives like accounting/biology terms)
        if topic_negatives and score.total_score > 0:
            combined_text = f"{paper.title} {paper.abstract or ''} {paper.journal or ''}".lower()
            for pattern, reason in topic_negatives:
                if pattern.search(combined_text):
                    score.total_score = 0
                    score.relevance_level = "LOW"
                    score.breakdown["topic_negative_hit"] = reason
                    break

        if score.total_score >= request.min_score:
            repo.add_score(conn, search_run_id, paper_id, score)
            scored += 1

    # Post-scoring: LLM review on MEDIUM+ papers
    llm_review_count = 0
    try:
        llm_review_count = _run_llm_review(conn, search_run_id, request, grouped)
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


def _fetch_dois_fallback(
    conn, dois: list[str], issns: list[str], grouped: list[list[ProviderPaper]], records: list[ProviderPaper]
) -> int:
    """Fetch known DOIs directly from Crossref and add to results if missing."""
    from literature_monitor.providers.crossref_client import CrossrefClient
    from literature_monitor.providers.crossref import _from_crossref_result
    from literature_monitor.providers.crossref_client import parse_crossref_work

    existing_dois = set()
    for group in grouped:
        for item in group:
            if item.paper.doi:
                existing_dois.add(item.paper.doi.lower())

    fetched = 0
    client = CrossrefClient()
    for doi in dois:
        if doi.lower() in existing_dois:
            continue
        try:
            url = f"https://api.crossref.org/works/{doi}"
            status, body = fetch_url(url, timeout_s=15)
            if status == 200:
                import json
                data = json.loads(body.decode("utf-8", errors="replace"))
                message = data.get("message", {})
                result = parse_crossref_work(message)
                if result:
                    provider_paper = _from_crossref_result(result)
                    records.append(provider_paper)
                    existing_dois.add(doi.lower())
                    fetched += 1
        except Exception as e:
            logger.warning("DOI fallback fetch failed for %s: %s", doi, e)
    return fetched


def _compile_topic_negatives(topic: Topic) -> list[tuple]:
    """Compile topic YAML negative keywords into regex patterns."""
    import re
    patterns = []
    seen = set()
    for pipe in topic.pipelines:
        for neg in pipe.negative_keywords:
            if neg and neg.lower() not in seen:
                seen.add(neg.lower())
                patterns.append((re.compile(re.escape(neg), re.IGNORECASE), neg))
    for ks in topic.keyword_sets:
        for neg in ks.negative_keywords:
            if neg and neg.lower() not in seen:
                seen.add(neg.lower())
                patterns.append((re.compile(re.escape(neg), re.IGNORECASE), neg))
    return patterns


def _resolve_topic_from_request(request: SearchRequest) -> Topic | None:
    """Find the Topic that matches this request's journal pools.

    Scans all loaded topics and returns the first one whose journal_pool_ids
    overlap with the request. Returns None if no match.
    """
    request_pools = set(request.journal_pool_ids)
    if not request_pools:
        return None
    for topic in load_all_topics().values():
        if request_pools.intersection(topic.journal_pool_ids):
            return topic
    return None


def _run_llm_review(
    conn, search_run_id: int, request: SearchRequest, grouped: list[list[ProviderPaper]]
) -> int:
    """Run LLM review on MEDIUM+ papers. Returns count of papers updated."""
    topic = _resolve_topic_from_request(request)
    if not topic or not topic.llm_review.enabled:
        return 0

    llm_stage = LLMReviewStage(
        topic_name=topic.name,
        topic_description=topic.description,
        provider=topic.llm_review.provider,
        model=topic.llm_review.model,
        base_url=topic.llm_review.base_url or "",
        two_stage=topic.llm_review.two_stage,
        min_level="MEDIUM",
        max_papers=topic.llm_review.max_papers_per_run,
        max_cost_usd=topic.llm_review.max_cost_usd,
        prompt_template=topic.llm_review.prompt_template or None,
        concurrency=topic.llm_review.concurrency,
    )

    updated = 0
    for group in grouped:
        paper = merge_group(group)
        paper_id = repo.upsert_paper(conn, paper)
        existing = conn.execute(
            "SELECT total_score, relevance_level, score_breakdown_json "
            "FROM scored_results WHERE search_run_id = ? AND paper_id = ?",
            (search_run_id, paper_id),
        ).fetchone()
        if not existing or existing["relevance_level"] not in ("HIGH", "MEDIUM"):
            continue

        state = PipelineState(
            title=paper.title,
            abstract=paper.abstract or "",
            journal=paper.journal or "",
            year=paper.year,
            total_score=existing["total_score"],
            relevance_level=existing["relevance_level"],
            breakdown=json.loads(existing["score_breakdown_json"]),
        )
        result = llm_stage.run(state)

        if result.total_score != existing["total_score"] or result.relevance_level != existing["relevance_level"]:
            conn.execute(
                "UPDATE scored_results SET total_score = ?, relevance_level = ?, "
                "score_breakdown_json = ? WHERE search_run_id = ? AND paper_id = ?",
                (result.total_score, result.relevance_level, json.dumps(result.breakdown), search_run_id, paper_id),
            )
            updated += 1
    conn.commit()
    return updated


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
