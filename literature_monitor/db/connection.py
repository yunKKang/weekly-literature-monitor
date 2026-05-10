"""SQLite connection helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from literature_monitor.compat import ROOT

DEFAULT_DB_PATH = ROOT / "data" / "literature_monitor.sqlite3"


def get_db_path() -> Path:
    return Path(os.environ.get("LITMON_DB_PATH", str(DEFAULT_DB_PATH)))


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

