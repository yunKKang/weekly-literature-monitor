"""SQLite connection helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from literature_monitor.config import ROOT

DEFAULT_DB_PATH = ROOT / "data" / "literature_monitor.sqlite3"


def get_db_path() -> Path:
    import os
    return Path(os.environ.get("LITMON_DB_PATH", str(DEFAULT_DB_PATH)))


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_connection(path: Path | None = None):
    """Context manager that yields a connection and closes it on exit."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()
