#!/usr/bin/env python3
"""Export papers to CSV for manual validation and threshold calibration."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from paper_utils import (
    get_issn_list,
    get_issn_by_tier,
    get_conference_titles,
    today_str,
    days_ago,
)
from crossref_client import fetch_recent_papers, fetch_conference_papers
from relevance_filter import load_keyword_config, score_paper


def export_validation_set(
    days_back: int = 14,
    sample_size: int = 200,
    output_path: str = "validation_set.csv",
    tier: str | None = None,
) -> int:
    base_dir = Path(__file__).parent.parent
    config_path = base_dir / "config"

    to_date = today_str()
    from_date = days_ago(days_back)

    print(f"Fetching papers from {from_date} to {to_date}...")

    if tier:
        tier_list = [t.strip().upper() for t in tier.split(",")]
        issns, tier_counts = get_issn_by_tier(tier_list, config_path / "journals.json")
        print(f"Tiers {tier_list}: {len(issns)} journals")
    else:
        issns = get_issn_list(config_path / "journals.json")
        tier_list = None
        print(f"All {len(issns)} journals")

    all_papers = fetch_recent_papers(
        issns=issns,
        from_date=from_date,
        to_date=to_date,
        max_per_journal=50,
        delay_s=0.3,
    )

    conferences = get_conference_titles(tier_list, config_path / "journals.json")
    if conferences:
        container_titles = [c["container_title"] for c in conferences]
        conf_papers = fetch_conference_papers(
            container_titles=container_titles,
            from_date=from_date,
            to_date=to_date,
            max_per_conference=30,
            delay_s=0.3,
        )
        seen_dois = {p.doi for p in all_papers}
        all_papers.extend([p for p in conf_papers if p.doi not in seen_dois])

    print(f"Fetched {len(all_papers)} papers total")

    keyword_config = load_keyword_config(config_path / "keywords.json")

    rows = []
    for paper in all_papers:
        title = paper.title or ""
        abstract = paper.abstract or ""
        result = score_paper(title, abstract, keyword_config)

        rows.append(
            {
                "doi": paper.doi,
                "title": title[:200],
                "abstract": abstract[:500],
                "journal": paper.journal or "",
                "year": paper.year or "",
                "score": result.score,
                "priority": result.priority,
                "passed_threshold": "yes" if result.score > 0 else "no",
                "investment_terms": "|".join(result.matched_investment_terms),
                "domain_terms": "|".join(result.matched_domain_terms),
                "pipelines": "|".join(result.matched_pipelines),
                "asset_types": "|".join(result.matched_asset_types),
                "negative_matches": "|".join(result.negative_matches),
                "passed_consistency": "yes" if result.passed_consistency else "no",
                "label": "",
            }
        )

    rows.sort(key=lambda x: (-x["score"], x["title"]))

    if sample_size and len(rows) > sample_size:
        rows = rows[:sample_size]

    output = Path(output_path)
    fieldnames = [
        "doi",
        "title",
        "abstract",
        "journal",
        "year",
        "score",
        "priority",
        "passed_threshold",
        "investment_terms",
        "domain_terms",
        "pipelines",
        "asset_types",
        "negative_matches",
        "passed_consistency",
        "label",
    ]

    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} papers to {output}")

    priority_counts = {}
    for r in rows:
        p = r["priority"]
        priority_counts[p] = priority_counts.get(p, 0) + 1

    print(f"Distribution: {priority_counts}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export papers to CSV for validation set creation"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of days to look back (default: 14)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=200,
        help="Maximum papers to export (default: 200)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="validation_set.csv",
        help="Output CSV file path (default: validation_set.csv)",
    )
    parser.add_argument(
        "--tier",
        type=str,
        default=None,
        help="Monitoring tier(s) to query: S, A, B. Comma-separated.",
    )

    args = parser.parse_args()

    return export_validation_set(
        days_back=args.days,
        sample_size=args.sample_size,
        output_path=args.output,
        tier=args.tier,
    )


if __name__ == "__main__":
    sys.exit(main())
