"""Text relevance stage — FTS5 BM25 text matching.

Requires a SQLite connection with paper_fts table.
Score is min(30.0, abs(bm25) * 10.0) — same formula as legacy.

Stage name: text_relevance
"""

from __future__ import annotations

import re
import sqlite3

from literature_monitor.pipeline.base import PipelineState


class TextRelevanceStage:
    """FTS5 BM25 text relevance scoring.

    Stage name: text_relevance
    """

    name = "text_relevance"

    def __init__(
        self,
        conn: sqlite3.Connection,
        query_text: str = "",
        weight: float = 0.6,
    ) -> None:
        self.conn = conn
        self.query_text = query_text
        self.weight = weight

    def run(self, state: PipelineState) -> PipelineState:
        query = self.query_text.strip()
        if not query:
            return state

        tokens = [t for t in re.findall(r"[\w一-鿿]+", query) if t]
        if not tokens:
            return state

        fts_query = " OR ".join(f'"{t}"' for t in tokens[:8])
        try:
            row = self.conn.execute(
                "SELECT bm25(paper_fts) AS rank FROM paper_fts"
                " WHERE rowid = ? AND paper_fts MATCH ?",
                (state.paper_id, fts_query),
            ).fetchone()
        except sqlite3.OperationalError:
            return state

        if not row:
            return state

        rank = float(row["rank"])
        text_score = min(30.0, abs(rank) * 10.0)

        breakdown = dict(state.breakdown)
        breakdown["text_score"] = text_score
        return state.with_(text_score=text_score, breakdown=breakdown)
