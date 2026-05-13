#!/usr/bin/env python3
"""Feedback script for iterative LLM prompt and keyword tuning.

Usage:
    # Mark a paper as NOT relevant (false positive)
    .venv/bin/python scripts/feedback.py --run-id 22 --paper-id 42 --verdict not-relevant \
        --reason "Uses 'investment' in biological sense, not GFCF"

    # Mark a paper as relevant (false negative / missed)
    .venv/bin/python scripts/feedback.py --verdict relevant --doi "10.1073/pnas.2218828120" \
        --reason "Studies legacy environmental footprint of manufactured capital using MRIO"

    # Show all feedback
    .venv/bin/python scripts/feedback.py --show

    # Export feedback as few-shot examples
    .venv/bin/python scripts/feedback.py --export-examples

    # Inject feedback examples into topic YAML prompt_template
    .venv/bin/python scripts/feedback.py --update-prompt
    .venv/bin/python scripts/feedback.py --update-prompt --topic-id algal_bloom_ml
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "literature_monitor.sqlite3"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_feedback_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_run_id INTEGER,
            paper_id INTEGER,
            doi TEXT,
            title TEXT,
            verdict TEXT NOT NULL CHECK (verdict IN ('relevant', 'not-relevant')),
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def add_feedback(
    conn: sqlite3.Connection,
    search_run_id: int | None,
    paper_id: int | None,
    doi: str | None,
    title: str | None,
    verdict: str,
    reason: str,
) -> None:
    conn.execute(
        "INSERT INTO paper_feedback (search_run_id, paper_id, doi, title, verdict, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (search_run_id, paper_id, doi, title, verdict, reason),
    )
    conn.commit()


def show_feedback(conn: sqlite3.Connection, search_run_id: int | None = None) -> list[dict]:
    if search_run_id:
        rows = conn.execute(
            "SELECT * FROM paper_feedback WHERE search_run_id = ? ORDER BY created_at DESC",
            (search_run_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM paper_feedback ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


def export_examples(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Export feedback as structured examples."""
    rows = conn.execute(
        "SELECT title, reason, verdict, doi FROM paper_feedback ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def format_examples_block(examples: list[dict[str, str]]) -> str:
    """Format examples into a prompt-ready block."""
    if not examples:
        return ""

    lines = ["## Examples (from user feedback)\n"]
    for ex in examples[:10]:  # max 10 examples
        title = ex["title"] or ex.get("doi", "unknown")
        label = "RELEVANT" if ex["verdict"] == "relevant" else "NOT RELEVANT"
        lines.append(f'{label}: "{title}" — {ex["reason"]}')
    return "\n".join(lines)


def update_prompt_with_feedback(
    topic_id: str = "gfcf_environment",
    examples: list[dict[str, str]] | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Inject feedback examples into topic YAML prompt_template.

    Finds the '## Examples' section in the prompt_template and replaces it
    with the latest feedback examples. If no Examples section exists, appends one.

    Returns: summary of what changed.
    """
    import yaml

    yaml_path = ROOT / "topics" / f"{topic_id}.yaml"
    if not yaml_path.exists():
        return f"ERROR: Topic YAML not found: {yaml_path}"

    if examples is None:
        if conn is None:
            conn = get_conn()
        examples = export_examples(conn)

    if not examples:
        return "No feedback examples to inject."

    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    llm = data.get("llm_review", {})
    prompt = llm.get("prompt_template", "")
    if not prompt:
        return "ERROR: No prompt_template in topic YAML."

    # Build new examples block
    new_block = format_examples_block(examples)

    # Replace or append the Examples section
    import re
    if "## Examples" in prompt:
        # Replace existing Examples section (from ## Examples to next ## section)
        prompt = re.sub(
            r"## Examples.*?(?=\n## |\Z)",
            new_block + "\n\n",
            prompt,
            flags=re.DOTALL,
        )
    else:
        # Insert before "## Paper to Evaluate"
        if "## Paper to Evaluate" in prompt:
            prompt = prompt.replace(
                "## Paper to Evaluate",
                new_block + "\n\n## Paper to Evaluate",
            )
        else:
            # Append at end
            prompt = prompt.rstrip() + "\n\n" + new_block

    # Write back to YAML
    llm["prompt_template"] = prompt.rstrip() + "\n"
    data["llm_review"] = llm

    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120)

    relevant_count = sum(1 for e in examples if e["verdict"] == "relevant")
    not_relevant_count = sum(1 for e in examples if e["verdict"] == "not-relevant")
    return (
        f"Updated {yaml_path.name} prompt_template:\n"
        f"  {relevant_count} RELEVANT examples\n"
        f"  {not_relevant_count} NOT RELEVANT examples\n"
        f"  Total: {len(examples)} examples injected"
    )


def main():
    parser = argparse.ArgumentParser(description="Feedback for iterative tuning")
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--paper-id", type=int, default=None)
    parser.add_argument("--doi", type=str, default=None)
    parser.add_argument("--title", type=str, default=None)
    parser.add_argument("--verdict", choices=["relevant", "not-relevant"])
    parser.add_argument("--reason", type=str, default="")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--export-examples", action="store_true")
    parser.add_argument("--update-prompt", action="store_true")
    parser.add_argument("--topic-id", type=str, default="gfcf_environment")
    args = parser.parse_args()

    conn = get_conn()
    init_feedback_table(conn)

    if args.show:
        feedback = show_feedback(conn, args.run_id)
        if not feedback:
            print("No feedback yet.")
            return
        for f in feedback:
            doi = f.get("doi") or "N/A"
            print(f'[{f["verdict"]}] {f["title"] or doi}')
            print(f'  Reason: {f["reason"]}')
            print(f'  Date: {f["created_at"]}')
            print()
        return

    if args.export_examples:
        examples = export_examples(conn)
        if not examples:
            print("(no feedback examples yet)")
            return
        print(format_examples_block(examples))
        return

    if args.update_prompt:
        result = update_prompt_with_feedback(topic_id=args.topic_id, conn=conn)
        print(result)
        return

    if args.verdict and args.reason:
        title = args.title
        if args.doi and not title:
            row = conn.execute(
                "SELECT title FROM papers WHERE doi LIKE ?", (f"%{args.doi}%",)
            ).fetchone()
            if row:
                title = row["title"]

        add_feedback(conn, args.run_id, args.paper_id, args.doi, title, args.verdict, args.reason)
        print(f"Feedback recorded: [{args.verdict}] {title or args.doi}")
        print(f"Reason: {args.reason}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
