"""Async SQLite connection using aiosqlite."""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_DB_PATH = ROOT / "data" / "literature_monitor.sqlite3"


def get_db_path() -> Path:
    return Path(os.environ.get("LITMON_DB_PATH", str(DEFAULT_DB_PATH)))


async def async_connect(path: Path | None = None) -> aiosqlite.Connection:
    """Create an async SQLite connection with aiosqlite."""
    db_path = path or get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn
