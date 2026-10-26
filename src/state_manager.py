#!/usr/bin/env python3
"""State management for weekly literature monitoring.

Tracks:
- Last run date
- Seen paper DOIs (for deduplication) - stored as ordered list, newest last
- Statistics

DOI Ordering Strategy:
- DOIs are appended in first-seen order (newest last)
- Re-encountered DOIs are NOT moved to end (no LRU promotion)
- When list exceeds MAX_SEEN_DOIS, oldest entries are evicted (FIFO)
- This ensures stable deduplication without unbounded memory growth
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import config
from paper_utils import normalize_doi


@dataclass
class MonitorState:
    last_run_date: str | None = None
    last_from_date: str | None = None
    seen_dois_list: list[str] = field(default_factory=list)
    total_papers_found: int = 0
    total_relevant_papers: int = 0
    run_count: int = 0

    @property
    def seen_dois(self) -> set[str]:
        return {info.full for doi in self.seen_dois_list if (info := normalize_doi(doi))}


def get_state_path(base_dir: Path | None = None) -> Path:
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    return base_dir / "state" / "monitor_state.json"


def load_state(state_path: Path | None = None) -> MonitorState:
    if state_path is None:
        state_path = get_state_path()

    if not state_path.exists():
        return MonitorState()

    try:
        with state_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        raise RuntimeError(f"Cannot read monitor state: {state_path}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("seen_dois", []), list):
        raise ValueError(f"Invalid monitor state: {state_path}")

    return MonitorState(
        last_run_date=data.get("last_run_date"),
        last_from_date=data.get("last_from_date"),
        seen_dois_list=data.get("seen_dois", []),
        total_papers_found=data.get("total_papers_found", 0),
        total_relevant_papers=data.get("total_relevant_papers", 0),
        run_count=data.get("run_count", 0),
    )


def save_state(state: MonitorState, state_path: Path | None = None) -> None:
    if state_path is None:
        state_path = get_state_path()

    state_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "last_run_date": state.last_run_date,
        "last_from_date": state.last_from_date,
        "seen_dois": state.seen_dois_list,
        "total_papers_found": state.total_papers_found,
        "total_relevant_papers": state.total_relevant_papers,
        "run_count": state.run_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Replace atomically so an interrupted runner cannot leave invalid JSON.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.", dir=str(state_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, state_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def update_state_after_run(
    state: MonitorState,
    run_date: str,
    from_date: str,
    new_dois: list[str],
    relevant_count: int,
) -> MonitorState:
    state.last_run_date = run_date
    state.last_from_date = from_date

    # Keep the existing FIFO order while normalizing legacy DOI variants.
    state.seen_dois_list = list(dict.fromkeys(
        info.full for doi in state.seen_dois_list if (info := normalize_doi(doi))
    ))
    existing = set(state.seen_dois_list)
    added = 0
    for doi in new_dois:
        info = normalize_doi(doi)
        if info and info.full not in existing:
            state.seen_dois_list.append(info.full)
            existing.add(info.full)
            added += 1

    state.total_papers_found += added
    state.total_relevant_papers += relevant_count
    state.run_count += 1

    if len(state.seen_dois_list) > config.MAX_SEEN_DOIS:
        state.seen_dois_list = state.seen_dois_list[-config.MAX_SEEN_DOIS :]

    return state
