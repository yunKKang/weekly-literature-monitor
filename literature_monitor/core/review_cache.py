"""Versioned review evidence. Acquisition and publication are separate states."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from literature_monitor.config import ROOT
from literature_monitor.core.models import Paper


def rules_version() -> str:
    digest = hashlib.sha256()
    for name in ("config/keywords.json", "topics/gfcf_environment.yaml"):
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()[:20]


class ReviewCache:
    def __init__(self, path: Path | None = None, conn=None):
        self.path, self.conn = path, conn
        self.entries = {}
        if path and path.exists():
            value = json.loads(path.read_text())
            if value.get("schema") != 1 or not isinstance(value.get("entries"), dict):
                raise ValueError("Invalid review cache")
            self.entries = value["entries"]
        if conn is not None:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ai_review_cache (cache_key TEXT PRIMARY KEY, data_json TEXT NOT NULL)"
            )

    def get(self, key):
        if self.conn is not None:
            row = self.conn.execute(
                "SELECT data_json FROM ai_review_cache WHERE cache_key=?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else None
        return self.entries.get(key)

    def put(self, key, value):
        if self.conn is not None:
            self.conn.execute(
                "INSERT OR REPLACE INTO ai_review_cache VALUES (?,?)",
                (key, json.dumps(value)),
            )
        else:
            self.entries[key] = value

    def key(self, state, provider, model, prompt):
        value = [
            "review-v1",
            rules_version(),
            provider,
            model,
            prompt,
            state.doi,
            state.title,
            state.abstract,
            state.journal,
            state.year,
        ]
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False).encode()
        ).hexdigest()

    def candidates(self) -> list[Paper]:
        return [
            Paper(**entry["paper"])
            for entry in self.entries.values()
            if entry.get("paper")
        ]

    def save(self):
        if self.conn is not None or not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(
                    {"schema": 1, "entries": self.entries},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def remember_paper(self, key, paper: Paper, review: dict):
        self.put(key, {"paper": asdict(paper), "review": review})
