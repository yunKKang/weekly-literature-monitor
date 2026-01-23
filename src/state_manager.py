#!/usr/bin/env python3
"""State management for weekly literature monitoring.

Tracks:
- Last run date
- Seen paper DOIs (for deduplication)
- Statistics
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class MonitorState:
    last_run_date: str | None = None
    last_from_date: str | None = None
    seen_dois: set[str] = field(default_factory=set)
    total_papers_found: int = 0
    total_relevant_papers: int = 0
    run_count: int = 0


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
    except (json.JSONDecodeError, IOError):
        return MonitorState()

    return MonitorState(
        last_run_date=data.get("last_run_date"),
        last_from_date=data.get("last_from_date"),
        seen_dois=set(data.get("seen_dois", [])),
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
        "seen_dois": list(state.seen_dois),
        "total_papers_found": state.total_papers_found,
        "total_relevant_papers": state.total_relevant_papers,
        "run_count": state.run_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    with state_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def update_state_after_run(
    state: MonitorState,
    run_date: str,
    from_date: str,
    new_dois: list[str],
    relevant_count: int,
) -> MonitorState:
    state.last_run_date = run_date
    state.last_from_date = from_date
    state.seen_dois.update(new_dois)
    state.total_papers_found += len(new_dois)
    state.total_relevant_papers += relevant_count
    state.run_count += 1

    MAX_SEEN_DOIS = 10000
    if len(state.seen_dois) > MAX_SEEN_DOIS:
        dois_list = list(state.seen_dois)
        state.seen_dois = set(dois_list[-MAX_SEEN_DOIS:])

    return state
