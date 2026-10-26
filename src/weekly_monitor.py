#!/usr/bin/env python3
"""Scheduled literature retrieval with versioned, auditable AI review."""

from __future__ import annotations
import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import config
from paper_utils import (
    today_str,
    days_ago,
    shift_date,
    normalize_doi,
    doi_to_url,
    get_issn_by_tier,
)
from crossref_client import fetch_recent_papers
from relevance_filter import load_keyword_config, filter_papers
from state_manager import load_state, save_state, update_state_after_run
from github_issue import notify_new_papers, PaperInfo
import github_issue
from literature_monitor.core.journals import (
    monitoring_issns,
    validate_catalog,
    resolve_issns,
)
from literature_monitor.core.models import Paper, SearchRequest
from literature_monitor.core.review_cache import ReviewCache, rules_version
from literature_monitor.core.review_service import review_papers
from literature_monitor.pipeline.base import PipelineState
from literature_monitor.pipeline.stages.llm_review import LLMReviewStage
from literature_monitor.topic.loader import load_topic

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def setup_logging(verbose=True):
    # Metadata services can carry API keys in query parameters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def check_ai():
    topic = load_topic("gfcf_environment")
    stage = LLMReviewStage(
        provider=topic.llm_review.provider,
        model=topic.llm_review.model,
        base_url=topic.llm_review.base_url,
        two_stage=False,
        prompt_template=topic.llm_review.prompt_template,
        topic_description=topic.description,
        max_cost_usd=0.05,
    )
    if not stage.api_key:
        logger.error(
            "DEEPSEEK_API_KEY is missing; configure a repository Actions secret."
        )
        return 2
    sample = PipelineState(
        title="Environmental footprints of gross fixed capital formation",
        abstract="This validation example quantifies the global supply-chain greenhouse-gas footprint of physical capital formation using MRIO and compares future IAM scenarios.",
        relevance_level="HIGH",
        total_score=40,
    )
    result = stage.run(sample)
    audit = result.breakdown.get("llm_review", {})
    good = audit.get("status") == "completed" and audit.get("relevant") is True
    logger.info(
        "AI connectivity: %s; model=%s", "passed" if good else "failed", stage.model
    )
    return 0 if good else 2


def enrich_abstract(paper):
    """Try DOI-linked OpenAlex metadata. Missing evidence stays pending."""
    if paper.abstract and len(paper.abstract.strip()) >= 50:
        return paper
    import os, urllib.parse
    from literature_monitor.providers.openalex import parse_openalex_work

    url = "https://api.openalex.org/works/https://doi.org/" + urllib.parse.quote(
        paper.doi or "", safe="/"
    )
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        url += "?api_key=" + urllib.parse.quote(key)
    # Do not use fetch_url's URL logging with a credential-bearing URL.
    import httpx

    try:
        response = httpx.get(url, timeout=15)
        if response.status_code == 200:
            found = parse_openalex_work(response.json())
            if found and found.paper.abstract:
                paper.abstract = found.paper.abstract
    except (httpx.HTTPError, ValueError):
        pass
    return paper


def run_monitor(
    days_back=7,
    dry_run=False,
    verbose=True,
    reset=False,
    debug_filter=False,
    tier=None,
    overlap_days=None,
    reprocess_since=None,
    include_supplementary=False,
):
    setup_logging(verbose)
    validate_catalog()
    state = load_state()
    to_date = today_str()
    overlap = config.OVERLAP_DAYS if overlap_days is None else overlap_days
    if days_back < 0 or overlap < 0:
        raise ValueError("Lookback and overlap must be nonnegative")
    if reprocess_since:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reprocess_since):
            raise ValueError("Reprocessing date must be YYYY-MM-DD")
        parsed = date.fromisoformat(reprocess_since)
        if parsed > date.fromisoformat(to_date):
            raise ValueError("Reprocessing cannot start in the future")
        from_date = reprocess_since
    elif reset or not state.last_from_date:
        from_date = days_ago(days_back)
    else:
        from_date = shift_date(state.last_from_date, -overlap)
    topic = load_topic("gfcf_environment")
    if not dry_run and not LLMReviewStage(provider=topic.llm_review.provider).api_key:
        logger.error(
            "Missing AI credentials; checkpoint is unchanged. Configure DEEPSEEK_API_KEY."
        )
        return 2

    cache = ReviewCache(ROOT / "state/review_cache.json")
    if tier:
        issns = get_issn_by_tier([t.strip().upper() for t in tier.split(",")])[0]
    else:
        issns = monitoring_issns(to_date, include_supplementary=include_supplementary)
    supplementary = not tier and len(issns) > len(resolve_issns(["main_pool"], []))
    if supplementary and not reprocess_since:
        monthly_start = (
            shift_date(state.last_supplementary_date, -overlap)
            if state.last_supplementary_date
            else days_ago(40)
        )
        from_date = min(from_date, monthly_start)
    if not issns:
        raise ValueError("No journals selected; check the requested tier")
    logger.info("Retrieval period: %s to %s", from_date, to_date)
    fetched = fetch_recent_papers(
        issns=issns,
        from_date=from_date,
        to_date=to_date,
        max_per_journal=config.MAX_PAPERS_PER_JOURNAL,
        delay_s=config.API_DELAY_SECONDS,
    )
    # Scheduled monitoring uses verified journal identities; conferences are disabled.
    by_doi = {}
    for raw in fetched:
        data = raw.to_dict()
        info = normalize_doi(raw.doi)
        if not info:
            continue
        by_doi.setdefault(
            info.full,
            Paper(
                doi=info.full,
                title=raw.title,
                abstract=data.get("abstract"),
                journal=raw.journal,
                year=raw.year,
                issn=(getattr(raw, "raw", {}).get("ISSN") or [None])[0],
                authors=data.get("authors", []),
                publication_date=data.get("publication_date"),
                url=doi_to_url(info.full),
            ),
        )
    # Seen DOI means observed metadata, never completed review/publication.
    # Retry older pending candidates even after the acquisition window moves.
    for paper in cache.candidates():
        if paper.doi:
            existing = by_doi.get(paper.doi)
            if existing is None:
                by_doi[paper.doi] = paper
            elif not existing.abstract and paper.abstract:
                existing.abstract = paper.abstract

    reported = set()
    if not dry_run or (config.GITHUB_TOKEN and config.GITHUB_REPOSITORY):
        try:
            reported = github_issue.reported_dois(
                github_issue.list_report_issues(
                    config.GITHUB_REPOSITORY, config.GITHUB_TOKEN
                )
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.error("Cannot reconcile published DOIs: %s", exc)
            return 1
    candidates = [p for doi, p in by_doi.items() if doi not in reported]
    kw = load_keyword_config(ROOT / "config/keywords.json")
    selected = filter_papers([vars(p) for p in candidates], kw, min_priority="MEDIUM")
    selected_dois = {p["doi"] for p, _ in selected}
    candidates = [p for p in candidates if p.doi in selected_dois]
    if not dry_run:
        candidates = [enrich_abstract(p) for p in candidates]
    request = SearchRequest(
        date_from=from_date,
        date_to=to_date,
        topic_id="gfcf_environment",
        journal_pool_ids=["main_pool"],
    )
    outcomes = review_papers(candidates, request, cache=cache, include_ai=not dry_run)
    infos, pending = [], 0
    for paper, pipeline_state, score in outcomes:
        if pipeline_state.excluded:
            continue
        audit = pipeline_state.breakdown.get("llm_review", {})
        if dry_run:
            logger.info(
                "[DRY RUN, rules only] %s: %s", score.relevance_level, paper.title
            )
            continue
        if audit.get("status") != "completed" or audit.get("confidence") == "low":
            pending += 1
            continue
        if not audit.get("relevant"):
            continue
        infos.append(
            PaperInfo(
                doi=paper.doi,
                title=paper.title,
                authors=paper.authors,
                journal=paper.journal,
                year=paper.year,
                score=round(score.total_score),
                priority=score.relevance_level,
                matched_tiers=score.matched_pipelines,
                matched_keywords=score.matched_keywords,
                url=doi_to_url(paper.doi),
                ai_status="completed",
                ai_reason=audit.get("reason", ""),
                ai_model=audit.get("model", topic.llm_review.model),
            )
        )
    logger.info(
        "Fetched=%d; rule candidates=%d; AI approved=%d; pending=%d",
        len(fetched),
        len(candidates),
        len(infos),
        pending,
    )
    if dry_run:
        return 0
    # Persist review evidence before any remote publication. The workflow commits
    # this queue even if publication fails; it never commits an invented checkpoint.
    cache.save()
    if not notify_new_papers(infos, from_date, to_date, len(fetched), len(issns)):
        return 1
    checkpoint = max(state.last_from_date or to_date, to_date)
    state = update_state_after_run(
        state, to_date, checkpoint, [p.doi for p in by_doi.values()], len(infos)
    )
    if supplementary:
        state.last_supplementary_date = to_date
    state.rules_version = rules_version()
    state.review_pending_count = pending
    if pending == 0:
        state.last_review_complete_date = to_date
    save_state(state)
    import os

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(
                f"## Literature monitoring\n\nFetched: {len(fetched)}; rule candidates: {len(candidates)}; AI approved: {len(infos)}; pending: {pending}.\n\nAcquisition checkpoint: {checkpoint}; last fully reviewed: {state.last_review_complete_date or 'not yet complete'}.\n"
            )
    logger.info(
        "Acquisition checkpoint=%s; pending reviews=%d; last fully reviewed=%s",
        checkpoint,
        pending,
        state.last_review_complete_date,
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--debug-filter", action="store_true")
    parser.add_argument("--tier")
    parser.add_argument("--overlap-days", type=int)
    parser.add_argument("--reprocess-since")
    parser.add_argument("--include-supplementary", action="store_true")
    parser.add_argument("--check-ai", action="store_true")
    args = parser.parse_args()
    if args.check_ai:
        setup_logging(False)
        return check_ai()
    return run_monitor(
        days_back=args.days,
        dry_run=args.dry_run,
        verbose=not args.quiet,
        reset=args.reset,
        debug_filter=args.debug_filter,
        tier=args.tier,
        overlap_days=args.overlap_days,
        reprocess_since=args.reprocess_since,
        include_supplementary=args.include_supplementary,
    )


if __name__ == "__main__":
    sys.exit(main())
