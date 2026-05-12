"""Command line entrypoint for the manual web app."""

from __future__ import annotations

import argparse
from datetime import date

from literature_monitor.core.search_service import (
    create_and_run_search,
    request_from_dict,
)
from literature_monitor.core.topics import list_search_topics
from literature_monitor.db.connection import connect
from literature_monitor.db.schema import init_db
from literature_monitor.topic.loader import load_topic, list_topics, validate_topic
from literature_monitor.topic.schema import Topic


# ---------------------------------------------------------------------------
# Topic subcommands
# ---------------------------------------------------------------------------

def _cmd_topic_list() -> int:
    topic_ids = list_topics()
    if not topic_ids:
        print("No topics found in topics/")
        return 0

    print(f"{'ID':<25} {'Name':<40} {'Pipelines'}")
    print("-" * 80)
    for tid in topic_ids:
        try:
            t = load_topic(tid)
            print(f"{t.id:<25} {t.name:<40} {len(t.pipelines)}")
        except Exception as e:
            print(f"{tid:<25} {'ERROR: ' + str(e)[:38]:<40}")
    return 0


def _cmd_topic_validate(topic_id: str) -> int:
    ok, msg = validate_topic(topic_id)
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {topic_id}: {msg}")
    return 0 if ok else 1


def _cmd_topic_show(topic_id: str) -> int:
    try:
        topic = load_topic(topic_id)
    except FileNotFoundError:
        print(f"Topic not found: {topic_id}")
        return 1

    print(f"Topic: {topic.name} ({topic.id})")
    print(f"Description: {topic.description.strip()}")
    print(f"Version: {topic.version}")
    print(f"Date range: {topic.date_range.date_from} → {topic.date_range.date_to or 'today'}")
    print(f"Journal pools: {', '.join(topic.journal_pool_ids) or '(none)'}")
    print(f"Keyword sets ({len(topic.keyword_sets)}):")
    for ks in topic.keyword_sets:
        print(f"  - {ks.name}: {len(ks.keywords)} keywords, "
              f"{len(ks.synonyms)} synonyms, {len(ks.negative_keywords)} negatives")
    print(f"Pipelines ({len(topic.pipelines)}):")
    for p in topic.pipelines:
        parts = []
        for s in p.hard_threshold.required_sets:
            if isinstance(s, list):
                parts.append(" OR ".join(s))
            else:
                parts.append(s)
        req = " AND ".join(f"({x})" if " OR " in x else x for x in parts)
        print(f"  [{p.priority}] {p.name} — threshold: {req}")
        if p.negative_keywords:
            print(f"      negatives: {len(p.negative_keywords)}")
    print(f"Scoring: title_w={topic.scoring.title_weight} abstract_w={topic.scoring.abstract_weight} "
          f"high≥{topic.scoring.high_threshold} medium≥{topic.scoring.medium_threshold}")
    print(f"Consistency check: {'on' if topic.scoring.consistency_check else 'off'}")
    if topic.llm_review.enabled:
        print(f"LLM review: {topic.llm_review.provider}/{topic.llm_review.model} "
              f"(max {topic.llm_review.max_papers_per_run} papers, ${topic.llm_review.max_cost_usd})")
    else:
        print("LLM review: off")
    print(f"Exporters: {', '.join(e.type for e in topic.exporters if e.enabled) or '(none)'}")
    return 0


# ---------------------------------------------------------------------------
# Search command — topic resolution
# ---------------------------------------------------------------------------

def _resolve_topic(topic_id: str) -> dict:
    """Try new Topic YAML first, then legacy search_topics.json fallback."""
    # Try new system
    for tid in list_topics():
        if tid == topic_id:
            t = load_topic(topic_id)
            # Flatten all keywords from all keyword_sets into a single list
            all_keywords = []
            all_synonyms = []
            all_negatives = []
            for ks in t.keyword_sets:
                all_keywords.extend(ks.keywords)
                all_synonyms.extend(ks.synonyms)
                all_negatives.extend(ks.negative_keywords)
            return {
                "id": t.id,
                "date_from": t.date_range.date_from,
                "keywords": all_keywords,
                "synonyms": all_synonyms,
                "negative_keywords": all_negatives,
                "journal_pool_ids": t.journal_pool_ids,
                "include_conferences": t.include_conferences,
                "min_score": 0,
                "max_results_per_source": 200,
            }

    # Fallback to legacy
    for topic in list_search_topics():
        if topic.get("id") == topic_id:
            return topic

    raise SystemExit(f"Unknown topic: {topic_id} (checked topics/ and config/search_topics.json)")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Manual literature search workbench")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("init-db", help="Initialize the SQLite database")

    search = sub.add_parser("search", help="Run one manual search")
    search.add_argument("--date-from")
    search.add_argument("--date-to")
    search.add_argument("--keywords", default="")
    search.add_argument("--topic", default="")
    search.add_argument("--journal-pool", action="append", default=[])
    search.add_argument("--issn", action="append", default=[])
    search.add_argument("--max-results-per-source", type=int)

    topic_parser = sub.add_parser("topic", help="Manage research topics")
    topic_sub = topic_parser.add_subparsers(dest="topic_command", required=True)
    topic_sub.add_parser("list", help="List all available topics")
    tv = topic_sub.add_parser("validate", help="Validate a topic YAML")
    tv.add_argument("topic_id", help="Topic ID to validate")
    ts = topic_sub.add_parser("show", help="Show topic details")
    ts.add_argument("topic_id", help="Topic ID to show")

    args = parser.parse_args()

    if args.command == "init-db":
        conn = connect()
        init_db(conn)
        print("Database initialized")
        return 0

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "literature_monitor.api.app:app",
            host=args.host,
            port=args.port,
            reload=False,
        )
        return 0

    if args.command == "topic":
        if args.topic_command == "list":
            return _cmd_topic_list()
        if args.topic_command == "validate":
            return _cmd_topic_validate(args.topic_id)
        if args.topic_command == "show":
            return _cmd_topic_show(args.topic_id)
        return 1

    if args.command == "search":
        preset = _resolve_topic(args.topic) if args.topic else {}
        keywords = args.keywords or ",".join(preset.get("keywords", []))
        journal_pools = args.journal_pool or preset.get("journal_pool_ids", [])
        request = request_from_dict(
            {
                "date_from": args.date_from or preset.get("date_from"),
                "date_to": args.date_to or date.today().isoformat(),
                "keywords": keywords,
                "synonyms": preset.get("synonyms", []),
                "negative_keywords": preset.get("negative_keywords", []),
                "journal_pool_ids": journal_pools,
                "journal_issns": args.issn,
                "include_conferences": preset.get("include_conferences", False),
                "min_score": preset.get("min_score", 0),
                "max_results_per_source": (
                    args.max_results_per_source
                    if args.max_results_per_source is not None
                    else preset.get("max_results_per_source", 100)
                ),
            }
        )
        summary = create_and_run_search(request)
        print(summary)
        return 0

    return 1


def topic_by_id(topic_id: str) -> dict:
    """Legacy helper — kept for backward compatibility."""
    return _resolve_topic(topic_id)


if __name__ == "__main__":
    raise SystemExit(main())
