#!/usr/bin/env python3
"""Baseline snapshot script for refactor regression testing.

Fetches papers from Crossref for a given topic, scores them using the legacy
scoring pipeline, and saves the output as a JSON baseline snapshot.

Usage:
    .venv/bin/python scripts/snapshot_baseline.py [--days 30] [--max-papers 50]

Output: baseline/<topic>_<date>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

import literature_monitor.core.utils  # noqa: E401
from literature_monitor.config import config  # noqa: E402
from literature_monitor.core.utils import get_issn_list, days_ago  # noqa: E402
from literature_monitor.providers.crossref_client import (  # noqa: E402
    search_crossref_page,
    SearchParams,
)
from literature_monitor.core.relevance_filter import (  # noqa: E402
    load_keyword_config,
    filter_papers,
    score_paper,
    RelevanceResult,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def safe_fetch_papers(
    issns: list[str],
    from_date: str,
    to_date: str,
    max_per_journal: int = 20,
    delay_s: float = 1.0,
    batch_size: int = 5,
) -> list[dict]:
    """Fetch recent papers with per-batch error tolerance.

    Unlike fetch_recent_papers(), individual batch failures are logged and
    skipped rather than aborting the entire run.
    """
    all_papers: list[dict] = []
    seen_dois: set[str] = set()
    failed_batches: list[str] = []

    for i in range(0, len(issns), batch_size):
        batch = issns[i : i + batch_size]
        rows = min(max_per_journal * len(batch), config.MAX_PAPERS_PER_BATCH)
        cursor = "*"
        pages_fetched = 0

        try:
            while True:
                params = SearchParams(
                    query="",
                    issns=batch,
                    year_from=from_date,
                    year_to=to_date,
                    max_results=rows,
                    sort_by="published",
                    sort_order="desc",
                    cursor=cursor,
                )
                page = search_crossref_page(params)
                pages_fetched += 1

                for r in page.results:
                    paper = r.to_dict()
                    doi = paper.get("doi", "")
                    if doi and doi not in seen_dois:
                        seen_dois.add(doi)
                        all_papers.append(paper)

                if not page.results:
                    break
                if not page.next_cursor or page.next_cursor == cursor:
                    break
                if pages_fetched >= config.MAX_CURSOR_PAGES:
                    break
                cursor = page.next_cursor

        except Exception as e:
            failed_batches.append(f"{batch[:3]}... ({e})")
            logger.warning("[baseline] Batch %s failed: %s", batch[:3], e)

        if delay_s > 0:
            time.sleep(delay_s)

    if failed_batches:
        logger.warning(
            "[baseline] %d/%d batches failed (continuing with partial results)",
            len(failed_batches),
            (len(issns) + batch_size - 1) // batch_size,
        )

    return all_papers


def paper_fingerprint(paper: dict) -> str:
    """Create a stable fingerprint for a paper dict."""
    doi = paper.get("doi", "")
    title = paper.get("title", "")
    raw = f"{doi}|{title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def serialize_result(paper: dict, result: RelevanceResult) -> dict:
    """Serialize a paper + RelevanceResult into a JSON-safe dict."""
    return {
        "fingerprint": paper_fingerprint(paper),
        "doi": paper.get("doi", ""),
        "title": paper.get("title", "")[:200],
        "journal": paper.get("journal", ""),
        "year": paper.get("year", None),
        "legacy_score": result.score,
        "legacy_priority": result.priority,
        "matched_pipelines": result.matched_pipelines,
        "matched_investment_terms": result.matched_investment_terms,
        "matched_domain_terms": result.matched_domain_terms,
        "matched_asset_types": result.matched_asset_types,
        "matched_themes": result.matched_themes,
        "negative_hits": result.negative_matches,
        "consistency_passed": result.passed_consistency,
        "abstract_snippet": (paper.get("abstract", "") or "")[:300],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate baseline snapshot for refactor regression testing"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days to look back (default: 30)"
    )
    parser.add_argument(
        "--max-papers", type=int, default=20,
        help="Max papers per journal batch (default: 20)"
    )
    parser.add_argument(
        "--issn-limit", type=int, default=30,
        help="Max ISSNs to query (default: 30 for speed)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Custom output path (default: auto-generated)"
    )
    args = parser.parse_args()

    print("[baseline] Loading keyword config...")
    kw_config = load_keyword_config()

    print("[baseline] Loading journal ISSN list...")
    issn_list = get_issn_list()
    if not issn_list:
        print("[ERROR] No ISSNs found in config/journals.json")
        sys.exit(1)

    issn_subset = issn_list[: args.issn_limit]
    print(f"[baseline] Using {len(issn_subset)}/{len(issn_list)} ISSNs")

    date_from = days_ago(args.days)
    date_to = days_ago(0)
    print(f"[baseline] Fetching papers from {date_from} to {date_to}...")

    start_time = time.time()
    papers = safe_fetch_papers(
        issn_subset,
        from_date=date_from,
        to_date=date_to,
        max_per_journal=args.max_papers,
        delay_s=1.0,
        batch_size=5,
    )
    fetch_time = time.time() - start_time
    print(f"[baseline] Fetched {len(papers)} papers in {fetch_time:.1f}s")

    if not papers:
        print("[WARN] No papers fetched. Check network / date range / ISSNs.")
        sys.exit(1)

    # Deduplicate by DOI
    seen_dois: dict[str, dict] = {}
    for p in papers:
        doi = p.get("doi", "")
        if doi and doi not in seen_dois:
            seen_dois[doi] = p
    unique_papers = list(seen_dois.values())
    print(f"[baseline] {len(unique_papers)} unique papers after DOI dedup")

    # Score using legacy system
    print("[baseline] Scoring with legacy pipeline...")
    scored_start = time.time()
    results = filter_papers(unique_papers, kw_config)
    score_time = time.time() - scored_start
    print(f"[baseline] Scored in {score_time:.1f}s")

    # Build snapshot
    snapshot_papers = []
    for paper, result in results:
        snapshot_papers.append(serialize_result(paper, result))

    # Count filtered-out papers
    scored_dois = {r["doi"] for r in snapshot_papers}
    unfiltered_count = sum(
        1 for p in unique_papers
        if p.get("doi", "") and p.get("doi", "") not in scored_dois
    )

    # Sort by score descending for stable ordering
    snapshot_papers.sort(key=lambda r: r["legacy_score"], reverse=True)

    snapshot = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "script": "scripts/snapshot_baseline.py",
            "args": {
                "days": args.days,
                "max_papers": args.max_papers,
                "issn_limit": args.issn_limit,
            },
            "date_range": {"from": date_from, "to": date_to},
            "issns_queried": len(issn_subset),
            "total_issns_available": len(issn_list),
            "papers_fetched": len(papers),
            "papers_unique": len(unique_papers),
            "papers_scored": len(snapshot_papers),
            "papers_filtered_out": unfiltered_count,
            "fetch_time_s": round(fetch_time, 2),
            "score_time_s": round(score_time, 2),
        },
        "papers": snapshot_papers,
    }

    # Write output
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = ROOT / "baseline" / f"gfcf_legacy_{today_str}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    print(f"\n[baseline] Snapshot saved to: {out_path}")
    print("[baseline] Summary:")
    print(f"  Papers fetched:  {len(papers)}")
    print(f"  Papers unique:   {len(unique_papers)}")
    print(f"  Papers scored:   {len(snapshot_papers)}")
    print(f"  Papers filtered: {unfiltered_count}")
    if snapshot_papers:
        scores = [p["legacy_score"] for p in snapshot_papers]
        high = sum(1 for p in snapshot_papers if p["legacy_priority"] == "HIGH")
        med = sum(1 for p in snapshot_papers if p["legacy_priority"] == "MEDIUM")
        low = sum(1 for p in snapshot_papers if p["legacy_priority"] == "LOW")
        print(f"  Score range:     {min(scores):.1f} – {max(scores):.1f}")
        print(f"  Priority dist:   HIGH={high}, MEDIUM={med}, LOW={low}")


if __name__ == "__main__":
    main()
