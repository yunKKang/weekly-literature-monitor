#!/usr/bin/env python3
"""Export literature monitor results to an Obsidian Markdown note."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from config import config
from crossref_client import CrossrefResult, fetch_conference_papers, fetch_recent_papers
from paper_utils import (
    doi_to_url,
    get_conference_titles,
    get_issn_by_tier,
    get_issn_list,
    today_str,
)
from relevance_filter import RelevanceResult, filter_papers, load_keyword_config
from state_manager import load_state, save_state, update_state_after_run

logger = logging.getLogger(__name__)


DEFAULT_OUTPUT_DIR = Path(os.environ.get("OBSIDIAN_OUTPUT_DIR", "obsidian-exports"))
DEFAULT_STATE_PATH = Path(__file__).parent.parent / "state" / "obsidian_monitor_state.json"


def setup_logging(verbose: bool = True) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def fetch_papers(
    from_date: str,
    to_date: str,
    tier: str | None = None,
) -> tuple[list[CrossrefResult], int]:
    base_dir = Path(__file__).parent.parent
    config_path = base_dir / "config"

    if tier:
        tier_list = [t.strip().upper() for t in tier.split(",")]
        issns, tier_counts = get_issn_by_tier(tier_list, config_path / "journals.json")
        tier_info = ", ".join(f"{t}:{c}" for t, c in sorted(tier_counts.items()))
        logger.info("Monitoring tiers %s: %s journals (%s)", tier_list, len(issns), tier_info)
    else:
        tier_list = None
        issns = get_issn_list(config_path / "journals.json")
        logger.info("Monitoring all %s journals", len(issns))

    all_papers = fetch_recent_papers(
        issns=issns,
        from_date=from_date,
        to_date=to_date,
        max_per_journal=config.MAX_PAPERS_PER_JOURNAL,
        delay_s=config.API_DELAY_SECONDS,
    )
    logger.info("Fetched %s journal papers", len(all_papers))

    conferences = get_conference_titles(tier_list, config_path / "journals.json")
    if conferences:
        conference_papers = fetch_conference_papers(
            container_titles=[c["container_title"] for c in conferences],
            from_date=from_date,
            to_date=to_date,
            max_per_conference=config.MAX_PAPERS_PER_CONFERENCE,
            delay_s=config.API_DELAY_SECONDS,
        )
        seen_dois = {p.doi for p in all_papers}
        new_conference_papers = [p for p in conference_papers if p.doi not in seen_dois]
        all_papers.extend(new_conference_papers)
        logger.info(
            "Fetched %s conference papers (%s new)",
            len(conference_papers),
            len(new_conference_papers),
        )

    return all_papers, len(issns)


def filter_by_score(
    papers: list[CrossrefResult],
    min_score: int,
) -> list[tuple[CrossrefResult, RelevanceResult]]:
    base_dir = Path(__file__).parent.parent
    keyword_config = load_keyword_config(base_dir / "config" / "keywords.json")
    paper_dicts = [paper.to_dict() for paper in papers]
    filtered = filter_papers(paper_dicts, keyword_config, min_priority="LOW")

    doi_to_paper = {paper.doi: paper for paper in papers}
    scored: list[tuple[CrossrefResult, RelevanceResult]] = []
    for paper_dict, relevance in filtered:
        if relevance.score < min_score:
            continue
        paper = doi_to_paper.get(paper_dict.get("doi", ""))
        if paper:
            scored.append((paper, relevance))

    scored.sort(
        key=lambda item: (
            -item[1].score,
            item[1].priority != "HIGH",
            item[0].publication_date or "",
            item[0].title,
        )
    )
    return scored


def sanitize_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]+', "-", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(". ")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find a unique path for {path}")


def format_authors(authors: list[dict[str, str]]) -> str:
    names = [author.get("name", "") for author in authors if author.get("name")]
    if not names:
        return "N/A"
    if len(names) <= 3:
        return ", ".join(names)
    return f"{', '.join(names[:3])} et al."


def format_terms(terms: list[str], limit: int = 10) -> str:
    if not terms:
        return "N/A"
    unique_terms = []
    for term in terms:
        if term not in unique_terms:
            unique_terms.append(term)
    return ", ".join(unique_terms[:limit])


def format_note(
    scored: list[tuple[CrossrefResult, RelevanceResult]],
    *,
    from_date: str,
    to_date: str,
    total_fetched: int,
    journals_count: int,
    min_score: int,
    include_seen: bool,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    generated_date = datetime.now().strftime("%Y-%m-%d")
    high = sum(1 for _, relevance in scored if relevance.priority == "HIGH")
    medium = sum(1 for _, relevance in scored if relevance.priority == "MEDIUM")
    low = sum(1 for _, relevance in scored if relevance.priority == "LOW")

    lines = [
        "---",
        f'title: "Literature monitor {from_date} to {to_date}"',
        f'date: "{generated_date}"',
        "tags:",
        "  - literature-monitor",
        "  - inbox",
        "source: weekly-literature-monitor",
        f'from_date: "{from_date}"',
        f'to_date: "{to_date}"',
        f"min_score: {min_score}",
        "---",
        "",
        f"# Literature Monitor {from_date} to {to_date}",
        "",
        "## Summary",
        "",
        f"- Generated: {generated}",
        f"- Total papers fetched: {total_fetched}",
        f"- Journals monitored: {journals_count}",
        f"- Papers with score >= {min_score}: {len(scored)}",
        f"- HIGH: {high}",
        f"- MEDIUM: {medium}",
        f"- LOW: {low}",
        f"- Included previously seen DOIs: {'yes' if include_seen else 'no'}",
        "",
    ]

    if not scored:
        lines.extend(
            [
                "## Papers",
                "",
                "No papers matched the score threshold in this period.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(["## Papers", ""])

    for index, (paper, relevance) in enumerate(scored, 1):
        doi_url = doi_to_url(paper.doi)
        date = paper.publication_date or paper.year or "N/A"
        lines.extend(
            [
                f"### {index}. {paper.title}",
                "",
                f"- Score: {relevance.score} ({relevance.priority})",
                f"- Published: {date}",
                f"- Authors: {format_authors(paper.authors)}",
                f"- Journal: {paper.journal or 'N/A'}",
                f"- DOI: [{paper.doi}]({doi_url})",
                f"- Pipeline: {relevance.matched_pipeline or 'N/A'}",
                f"- Matched terms: {format_terms(relevance.matched_keywords)}",
                f"- Asset types: {format_terms(relevance.matched_asset_types)}",
                f"- Themes: {format_terms(relevance.matched_themes)}",
                "",
            ]
        )

        if paper.abstract:
            abstract = paper.abstract.strip()
            if len(abstract) > 1200:
                abstract = abstract[:1197].rstrip() + "..."
            lines.extend(
                [
                    "> [!abstract]",
                    f"> {abstract}",
                    "",
                ]
            )

    return "\n".join(lines)


def write_note(content: str, output_dir: Path, from_date: str, to_date: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(f"literature-monitor-{from_date}_to_{to_date}.md")
    path = unique_path(output_dir / filename)
    path.write_text(content, encoding="utf-8")
    return path


def run_export(
    *,
    from_date: str,
    to_date: str,
    output_dir: Path,
    min_score: int,
    tier: str | None = None,
    include_seen: bool = False,
    update_state: bool = False,
    state_path: Path = DEFAULT_STATE_PATH,
) -> Path:
    logger.info("Date range: %s to %s", from_date, to_date)
    all_papers, journals_count = fetch_papers(from_date, to_date, tier=tier)

    state = load_state(state_path)
    papers_to_score = all_papers
    if not include_seen:
        papers_to_score = [paper for paper in all_papers if paper.doi not in state.seen_dois]
        logger.info("New papers not seen before: %s", len(papers_to_score))

    scored = filter_by_score(papers_to_score, min_score)
    logger.info("Papers with score >= %s: %s", min_score, len(scored))

    content = format_note(
        scored,
        from_date=from_date,
        to_date=to_date,
        total_fetched=len(all_papers),
        journals_count=journals_count,
        min_score=min_score,
        include_seen=include_seen,
    )
    note_path = write_note(content, output_dir, from_date, to_date)
    logger.info("Wrote Obsidian note: %s", note_path)

    if update_state:
        updated = update_state_after_run(
            state=state,
            run_date=to_date,
            from_date=to_date,
            new_dois=[paper.doi for paper in all_papers],
            relevant_count=len(scored),
        )
        save_state(updated, state_path)
        logger.info("State updated through %s", to_date)

    return note_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export weekly literature monitor results to Obsidian Markdown"
    )
    parser.add_argument("--from-date", help="Start date, YYYY-MM-DD")
    parser.add_argument("--to-date", help="End date, YYYY-MM-DD")
    parser.add_argument(
        "--from-state",
        action="store_true",
        help="Use monitor state's last_from_date as the start date",
    )
    parser.add_argument(
        "--to-today",
        action="store_true",
        help="Use today's date as the end date",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=5,
        help="Minimum relevance score to include (default: 5)",
    )
    parser.add_argument(
        "--tier",
        type=str,
        default=None,
        help="Monitoring tier(s), comma-separated, e.g. S,A",
    )
    parser.add_argument(
        "--include-seen",
        action="store_true",
        help="Include DOIs already present in monitor state",
    )
    parser.add_argument(
        "--update-state",
        action="store_true",
        help="Update monitor state after a successful export",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"State file for Obsidian exports (default: {DEFAULT_STATE_PATH})",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress log output")

    args = parser.parse_args()
    setup_logging(verbose=not args.quiet)

    state = load_state(args.state_path)
    from_date = args.from_date
    to_date = args.to_date

    if args.from_state:
        from_date = state.last_from_date
    if args.to_today:
        to_date = today_str()

    if not from_date:
        parser.error("--from-date is required unless --from-state is set and state has last_from_date")
    if not to_date:
        parser.error("--to-date is required unless --to-today is set")

    run_export(
        from_date=from_date,
        to_date=to_date,
        output_dir=args.output_dir,
        min_score=args.min_score,
        tier=args.tier,
        include_seen=args.include_seen,
        update_state=args.update_state,
        state_path=args.state_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
