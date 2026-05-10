#!/usr/bin/env python3
"""Migrate state/monitor_state.json seen_dois into SQLite seen_dois table.

Usage:
    .venv/bin/python scripts/migrate_state.py [--dry-run]

This script:
1. Reads state/monitor_state.json (legacy state file)
2. Inserts all seen_dois into the SQLite seen_dois table
3. Records last_run_date and last_from_date as metadata
4. Does NOT delete or modify the original JSON file (safety)

The seen_dois table has no row limit (unlike the 10000 FIFO cap in the JSON).
After migration, the new system will read from SQLite for deduplication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
STATE_PATH = ROOT / "state" / "monitor_state.json"
DB_PATH = ROOT / "data" / "literature_monitor.sqlite3"


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy state JSON to SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without writing")
    parser.add_argument("--topic-id", default="__global__", help="Topic ID for seen_dois (default: __global__)")
    args = parser.parse_args()

    if not STATE_PATH.exists():
        print(f"[migrate] State file not found: {STATE_PATH}")
        sys.exit(1)

    with STATE_PATH.open("r", encoding="utf-8") as f:
        state_data = json.load(f)

    seen_dois = state_data.get("seen_dois", [])
    last_run_date = state_data.get("last_run_date")
    last_from_date = state_data.get("last_from_date")
    run_count = state_data.get("run_count", 0)

    print(f"[migrate] State file: {STATE_PATH}")
    print(f"[migrate] seen_dois count: {len(seen_dois)}")
    print(f"[migrate] last_run_date: {last_run_date}")
    print(f"[migrate] run_count: {run_count}")

    # Deduplicate
    unique_dois = list(dict.fromkeys(d for d in seen_dois if d))
    dupes = len(seen_dois) - len(unique_dois)
    if dupes:
        print(f"[migrate] Duplicate DOIs removed: {dupes}")
    print(f"[migrate] Unique DOIs to insert: {len(unique_dois)}")

    if args.dry_run:
        print("[migrate] DRY RUN — no changes made")
        return

    if not DB_PATH.exists():
        print(f"[migrate] DB not found: {DB_PATH}")
        print("[migrate] Run: litmon init-db")
        sys.exit(1)

    import sqlite3
    from literature_monitor.db.schema import init_db

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    # Check existing count
    existing = conn.execute(
        "SELECT COUNT(*) FROM seen_dois WHERE topic_id = ?", (args.topic_id,)
    ).fetchone()[0]
    print(f"[migrate] Existing seen_dois for topic '{args.topic_id}': {existing}")

    # Insert in batches
    batch_size = 1000
    inserted = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for i in range(0, len(unique_dois), batch_size):
        batch = unique_dois[i : i + batch_size]
        for doi in batch:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO seen_dois (doi, topic_id, first_seen_at) VALUES (?, ?, ?)",
                    (doi.lower().strip(), args.topic_id, now),
                )
                inserted += 1
            except Exception:
                skipped += 1
        conn.commit()
        print(f"[migrate] Batch {i // batch_size + 1}: inserted {len(batch)}")

    # Store metadata as a special '__meta__' entry
    meta_doi = f"__meta__:last_run_date"
    conn.execute(
        "INSERT OR REPLACE INTO seen_dois (doi, topic_id, first_seen_at) VALUES (?, ?, ?)",
        (meta_doi, args.topic_id, last_run_date or now),
    )
    meta_doi2 = f"__meta__:run_count"
    conn.execute(
        "INSERT OR REPLACE INTO seen_dois (doi, topic_id, first_seen_at) VALUES (?, ?, ?)",
        (meta_doi2, args.topic_id, str(run_count)),
    )
    conn.commit()

    final_count = conn.execute(
        "SELECT COUNT(*) FROM seen_dois WHERE topic_id = ?", (args.topic_id,)
    ).fetchone()[0]

    conn.close()

    print(f"\n[migrate] Migration complete:")
    print(f"  DOIs inserted: {inserted}")
    print(f"  DOIs skipped:  {skipped}")
    print(f"  Total in DB:   {final_count}")
    print(f"  Topic ID:      {args.topic_id}")
    print(f"\n[migrate] Original state file preserved at: {STATE_PATH}")
    print(f"[migrate] After confirming migration, update weekly_monitor.py to use SQLite.")


if __name__ == "__main__":
    main()
