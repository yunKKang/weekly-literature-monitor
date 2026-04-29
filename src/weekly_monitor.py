#!/usr/bin/env python3
"""Weekly Literature Monitor - Main entry point.

Fetches recent papers from monitored journals, filters by relevance,
and creates GitHub Issues with the results.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from config import config
from paper_utils import (
    get_issn_list,
    get_issn_by_tier,
    get_tier_frequency,
    get_conference_titles,
    today_str,
    days_ago,
    shift_date,
    doi_to_url,
)
from crossref_client import fetch_recent_papers, fetch_conference_papers, CrossrefResult
from relevance_filter import load_keyword_config, filter_papers, RelevanceResult
from state_manager import load_state, save_state, update_state_after_run
from github_issue import notify_new_papers, PaperInfo

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = True) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if not verbose:
        level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def result_to_paper_info(
    paper: CrossrefResult, relevance: RelevanceResult
) -> PaperInfo:
    return PaperInfo(
        doi=paper.doi,
        title=paper.title,
        authors=[a.get("name", "") for a in paper.authors],
        journal=paper.journal,
        year=paper.year,
        score=relevance.score,
        priority=relevance.priority,
        matched_tiers=relevance.matched_tiers,
        matched_keywords=relevance.matched_keywords,
        url=doi_to_url(paper.doi),
    )


def run_monitor(
    days_back: int = 7,
    dry_run: bool = False,
    verbose: bool = True,
    reset: bool = False,
    debug_filter: bool = False,
    tier: str | None = None,
    overlap_days: int | None = None,
) -> int:
    setup_logging(verbose)
    base_dir = Path(__file__).parent.parent
    config_path = base_dir / "config"

    logger.info("=" * 60)
    logger.info("Weekly Literature Monitor")
    logger.info("=" * 60)

    state = load_state()

    to_date = today_str()

    if reset or not state.last_from_date:
        from_date = days_ago(days_back)
        if reset:
            logger.info(
                f"[RESET] Ignoring last run date, searching last {days_back} days"
            )
        else:
            logger.info(f"First run, searching last {days_back} days")
    else:
        from_date = state.last_from_date
        logger.info(f"Continuing from last run: {from_date}")

    effective_overlap_days = (
        config.OVERLAP_DAYS if overlap_days is None else overlap_days
    )
    if not reset and state.last_from_date and effective_overlap_days > 0:
        original_from_date = from_date
        from_date = shift_date(from_date, -effective_overlap_days)
        logger.info(
            f"Using {effective_overlap_days}-day overlap window: {original_from_date} -> {from_date}"
        )

    logger.info(f"Date range: {from_date} to {to_date}")

    if tier:
        tier_list = [t.strip().upper() for t in tier.split(",")]
        issns, tier_counts = get_issn_by_tier(tier_list, config_path / "journals.json")
        tier_info = ", ".join(f"{t}:{c}" for t, c in sorted(tier_counts.items()))
        logger.info(
            f"Monitoring tiers {tier_list}: {len(issns)} journals ({tier_info})"
        )
    else:
        issns = get_issn_list(config_path / "journals.json")
        logger.info(f"Monitoring all {len(issns)} journals...")

    logger.info("Fetching papers from Crossref...")

    all_papers = fetch_recent_papers(
        issns=issns,
        from_date=from_date,
        to_date=to_date,
        max_per_journal=config.MAX_PAPERS_PER_JOURNAL,
        delay_s=config.API_DELAY_SECONDS,
    )

    logger.info(f"Fetched {len(all_papers)} journal papers")

    tier_list_for_conf = [t.strip().upper() for t in tier.split(",")] if tier else None
    conferences = get_conference_titles(
        tier_list_for_conf, config_path / "journals.json"
    )

    if conferences:
        container_titles = [c["container_title"] for c in conferences]
        logger.info(f"Fetching from {len(conferences)} conferences...")

        conference_papers = fetch_conference_papers(
            container_titles=container_titles,
            from_date=from_date,
            to_date=to_date,
            max_per_conference=config.MAX_PAPERS_PER_CONFERENCE,
            delay_s=config.API_DELAY_SECONDS,
        )

        seen_dois = {p.doi for p in all_papers}
        new_conf_papers = [p for p in conference_papers if p.doi not in seen_dois]
        all_papers.extend(new_conf_papers)
        logger.info(
            f"Fetched {len(conference_papers)} conference papers ({len(new_conf_papers)} new)"
        )

    logger.info(f"Total papers: {len(all_papers)}")

    new_papers = [p for p in all_papers if p.doi not in state.seen_dois]

    logger.info(f"New papers (not seen before): {len(new_papers)}")

    if not new_papers:
        logger.info("No new papers found. Exiting.")
        if not dry_run:
            state = update_state_after_run(
                state=state,
                run_date=to_date,
                from_date=to_date,
                new_dois=[],
                relevant_count=0,
            )
            save_state(state)
            if verbose:
                logger.info(f"State updated. Total runs: {state.run_count}")
        return 0

    logger.info("Filtering by relevance...")

    keyword_config = load_keyword_config(config_path / "keywords.json")

    papers_for_filter = [p.to_dict() for p in new_papers]

    # Debug filter: show detailed info for first N papers
    if debug_filter:
        from relevance_filter import score_paper

        logger.info("\n[DEBUG FILTER] Analyzing first 10 papers:")
        for i, paper_dict in enumerate(papers_for_filter[:10], 1):
            title = paper_dict.get("title", "")[:80]
            abstract = paper_dict.get("abstract", "")
            result = score_paper(title, abstract, keyword_config)
            logger.info(f"\n  {i}. {title}...")
            logger.info(f"     Priority: {result.priority}, Score: {result.score}")
            logger.info(f"     Investment terms: {result.matched_investment_terms}")
            logger.info(f"     Domain terms: {result.matched_domain_terms}")
            logger.info(f"     Pipelines: {result.matched_pipelines}")
            if result.negative_matches:
                logger.info(f"     Negative matches: {result.negative_matches}")
        logger.info("")

    filtered_results = filter_papers(
        papers_for_filter, keyword_config, min_priority="LOW"
    )

    if verbose:
        high_count = len([r for _, r in filtered_results if r.priority == "HIGH"])
        med_count = len([r for _, r in filtered_results if r.priority == "MEDIUM"])
        low_count = len([r for _, r in filtered_results if r.priority == "LOW"])
        logger.info(f"  HIGH priority: {high_count}")
        logger.info(f"  MEDIUM priority: {med_count}")
        logger.info(f"  LOW priority: {low_count}")
        logger.info(f"  Total relevant: {len(filtered_results)}")

    doi_to_paper = {p.doi: p for p in new_papers}
    paper_infos: list[PaperInfo] = []

    for paper_dict, relevance in filtered_results:
        doi = paper_dict.get("doi", "")
        original_paper = doi_to_paper.get(doi)
        if original_paper:
            paper_infos.append(result_to_paper_info(original_paper, relevance))

    if dry_run:
        logger.info("[DRY RUN] Would create GitHub Issue with:")
        logger.info(f"  - {len(paper_infos)} relevant papers")
        logger.info(f"  - Date range: {from_date} to {to_date}")

        if paper_infos:
            logger.info("Top 5 papers:")
            for i, p in enumerate(paper_infos[:5], 1):
                logger.info(f"  {i}. [{p.priority}] {p.title[:60]}...")
                logger.info(
                    f"      Score: {p.score}, Tiers: {', '.join(p.matched_tiers)}"
                )
    else:
        if verbose:
            logger.info("Creating GitHub Issue...")

        success = notify_new_papers(
            papers=paper_infos,
            from_date=from_date,
            to_date=to_date,
            total_fetched=len(all_papers),
            journals_count=len(issns),
        )

        if not success:
            logger.warning("Failed to create GitHub Issue")
            return 1

    new_dois = [p.doi for p in new_papers]
    state = update_state_after_run(
        state=state,
        run_date=to_date,
        from_date=to_date,
        new_dois=new_dois,
        relevant_count=len(paper_infos),
    )

    if not dry_run:
        save_state(state)
        if verbose:
            logger.info(f"State updated. Total runs: {state.run_count}")

    if verbose:
        logger.info("Done!")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Weekly Literature Monitor - Fetch and filter new academic papers"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without creating GitHub Issue or updating state",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Ignore last run date, use --days to set date range",
    )
    parser.add_argument(
        "--debug-filter",
        action="store_true",
        help="Show detailed filtering debug info for first 10 papers",
    )
    parser.add_argument(
        "--tier",
        type=str,
        default=None,
        help="Monitoring tier(s) to query: S (weekly), A (biweekly), B (monthly). Comma-separated, e.g., 'S,A'",
    )
    parser.add_argument(
        "--overlap-days",
        type=int,
        default=None,
        help="Overlap days before last run date to catch delayed Crossref records (default: OVERLAP_DAYS env or 30)",
    )

    args = parser.parse_args()

    return run_monitor(
        days_back=args.days,
        dry_run=args.dry_run,
        verbose=not args.quiet,
        reset=args.reset,
        debug_filter=args.debug_filter,
        tier=args.tier,
        overlap_days=args.overlap_days,
    )


if __name__ == "__main__":
    sys.exit(main())
