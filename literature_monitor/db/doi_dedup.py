"""SQLite-backed DOI deduplication (replaces state/monitor_state.json seen_dois).

This module provides:
- seen_doi_exists(): check if a DOI has been seen before
- mark_doi_seen(): record a DOI as seen
- bulk_mark_seen(): batch insert DOIs
- get_dedup_stats(): summary statistics per topic
- migrate_from_json(): one-time import from legacy JSON state

Unlike the legacy FIFO system (MAX_SEEN_DOIS=10000), SQLite has no row limit.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def seen_doi_exists(conn: sqlite3.Connection, doi: str, topic_id: str = "__global__") -> bool:
    """Check if a DOI has already been seen for a given topic."""
    if not doi:
        return False
    row = conn.execute(
        "SELECT 1 FROM seen_dois WHERE doi = ? AND topic_id = ?",
        (doi.lower().strip(), topic_id),
    ).fetchone()
    return row is not None


def mark_doi_seen(
    conn: sqlite3.Connection,
    doi: str,
    topic_id: str = "__global__",
    first_seen_at: str | None = None,
) -> bool:
    """Record a DOI as seen. Returns True if newly inserted, False if already existed."""
    if not doi:
        return False
    ts = first_seen_at or datetime.now(timezone.utc).isoformat()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO seen_dois (doi, topic_id, first_seen_at) VALUES (?, ?, ?)",
            (doi.lower().strip(), topic_id, ts),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        return False


def bulk_mark_seen(
    conn: sqlite3.Connection,
    dois: list[str],
    topic_id: str = "__global__",
) -> int:
    """Batch insert DOIs. Returns count of newly inserted DOIs."""
    ts = datetime.now(timezone.utc).isoformat()
    count = 0
    for doi in dois:
        if not doi:
            continue
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO seen_dois (doi, topic_id, first_seen_at) VALUES (?, ?, ?)",
                (doi.lower().strip(), topic_id, ts),
            )
            count += cursor.rowcount
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return count


def bulk_check_seen(
    conn: sqlite3.Connection,
    dois: list[str],
    topic_id: str = "__global__",
) -> set[str]:
    """Return the subset of DOIs that have already been seen."""
    if not dois:
        return set()
    seen: set[str] = set()
    # Batch query with IN clause (chunk to avoid too many params)
    chunk_size = 500
    normalized = [d.lower().strip() for d in dois if d]
    for i in range(0, len(normalized), chunk_size):
        chunk = normalized[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT doi FROM seen_dois WHERE doi IN ({placeholders}) AND topic_id = ?",
            [*chunk, topic_id],
        ).fetchall()
        seen.update(row[0] for row in rows)
    return seen


def get_dedup_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Get dedup statistics per topic."""
    rows = conn.execute(
        "SELECT topic_id, COUNT(*) FROM seen_dois GROUP BY topic_id"
    ).fetchall()
    stats = {row[0]: row[1] for row in rows}
    stats["__total__"] = sum(stats.values())
    return stats


def migrate_from_json(conn: sqlite3.Connection, json_path: Path, topic_id: str = "__global__") -> int:
    """Import seen_dois from legacy state JSON file.

    Returns count of newly inserted DOIs.
    """
    if not json_path.exists():
        return 0

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    dois = data.get("seen_dois", [])
    if not dois:
        return 0

    # Deduplicate (case-insensitive)
    unique = list(dict.fromkeys(d.lower().strip() for d in dois if d))

    # Batch insert
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for doi in unique:
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO seen_dois (doi, topic_id, first_seen_at) VALUES (?, ?, ?)",
                (doi, topic_id, now),
            )
            inserted += cursor.rowcount
        except sqlite3.IntegrityError:
            pass
    conn.commit()

    return inserted
