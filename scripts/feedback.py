#!/usr/bin/env python3
"""Feedback script for iterative LLM prompt and keyword tuning.

Usage:
    # Mark a paper as NOT relevant (false positive)
    .venv/bin/python scripts/feedback.py --run-id 22 --paper-id 42 --verdict not-relevant \
        --reason "Uses 'investment' in biological sense, not GFCF"

    # Mark a paper as relevant (false negative / missed)
    .venv/bin/python scripts/feedback.py --verdict relevant --doi "10.1073/pnas.2218828120" \
        --reason "Studies legacy environmental footprint of manufactured capital using MRIO"

    # Show all feedback for a run
    .venv/bin/python scripts/feedback.py --run-id 22 --show

    # Export feedback as few-shot examples for LLM prompt
    .venv/bin/python scripts/feedback.py --export-examples
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


def export_examples(conn: sqlite3.Connection) -> str:
    """Export feedback as few-shot examples for LLM prompt."""
    relevant = conn.execute(
        "SELECT title, reason FROM paper_feedback WHERE verdict = 'relevant' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    not_relevant = conn.execute(
        "SELECT title, reason FROM paper_feedback WHERE verdict = 'not-relevant' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    examples = []
    for r in relevant:
        examples.append(f'RELEVANT: "{r["title"]}" - {r["reason"]}')
    for r in not_relevant:
        examples.append(f'NOT RELEVANT: "{r["title"]}" - {r["reason"]}')

    return "\n".join(examples) if examples else "(no feedback examples yet)"


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
        print(export_examples(conn))
        return

    if args.verdict and args.reason:
        # If --doi but no --title, look up title from papers table
        title = args.title
        if args.doi and not title:
            row = conn.execute("SELECT title FROM papers WHERE doi LIKE ?", (f"%{args.doi}%",)).fetchone()
            if row:
                title = row["title"]

        add_feedback(conn, args.run_id, args.paper_id, args.doi, title, args.verdict, args.reason)
        print(f"Feedback recorded: [{args.verdict}] {title or args.doi}")
        print(f"Reason: {args.reason}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
