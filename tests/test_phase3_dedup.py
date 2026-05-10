"""Tests for Phase 3: SQLite-backed DOI deduplication and state migration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from literature_monitor.db.doi_dedup import (
    seen_doi_exists,
    mark_doi_seen,
    bulk_mark_seen,
    bulk_check_seen,
    get_dedup_stats,
    migrate_from_json,
)
from literature_monitor.db.schema import init_db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


class TestSeenDoiExists:
    def test_not_seen(self, conn):
        assert seen_doi_exists(conn, "10.1234/test") is False

    def test_seen_after_mark(self, conn):
        mark_doi_seen(conn, "10.1234/test")
        assert seen_doi_exists(conn, "10.1234/test") is True

    def test_case_insensitive(self, conn):
        mark_doi_seen(conn, "10.1234/TEST")
        assert seen_doi_exists(conn, "10.1234/test") is True

    def test_topic_isolation(self, conn):
        mark_doi_seen(conn, "10.1234/test", topic_id="gfcf")
        assert seen_doi_exists(conn, "10.1234/test", topic_id="gfcf") is True
        assert seen_doi_exists(conn, "10.1234/test", topic_id="algal") is False

    def test_global_default(self, conn):
        mark_doi_seen(conn, "10.1234/x")
        assert seen_doi_exists(conn, "10.1234/x", topic_id="__global__") is True


class TestMarkDoiSeen:
    def test_insert(self, conn):
        mark_doi_seen(conn, "10.1234/a")
        assert seen_doi_exists(conn, "10.1234/a")

    def test_duplicate_ignored(self, conn):
        mark_doi_seen(conn, "10.1234/a")
        mark_doi_seen(conn, "10.1234/a")
        stats = get_dedup_stats(conn)
        assert stats["__global__"] == 1


class TestBulkMarkSeen:
    def test_batch_insert(self, conn):
        dois = [f"10.1234/paper{i}" for i in range(50)]
        count = bulk_mark_seen(conn, dois)
        assert count == 50
        stats = get_dedup_stats(conn)
        assert stats["__global__"] == 50

    def test_empty_list(self, conn):
        assert bulk_mark_seen(conn, []) == 0

    def test_deduplicates(self, conn):
        bulk_mark_seen(conn, ["10.1234/a", "10.1234/b"])
        bulk_mark_seen(conn, ["10.1234/b", "10.1234/c"])
        stats = get_dedup_stats(conn)
        assert stats["__global__"] == 3


class TestBulkCheckSeen:
    def test_mixed(self, conn):
        mark_doi_seen(conn, "10.1234/a")
        mark_doi_seen(conn, "10.1234/b")
        result = bulk_check_seen(conn, ["10.1234/a", "10.1234/b", "10.1234/c"])
        assert "10.1234/a" in result
        assert "10.1234/b" in result
        assert "10.1234/c" not in result

    def test_empty(self, conn):
        assert bulk_check_seen(conn, []) == set()


class TestMigrateFromJson:
    def test_migrates_dois(self, conn, tmp_path):
        state = {"seen_dois": ["10.1/a", "10.2/b", "10.3/c"], "last_run_date": "2026-05-01"}
        json_file = tmp_path / "state.json"
        json_file.write_text(json.dumps(state))

        count = migrate_from_json(conn, json_file, topic_id="gfcf")
        assert count == 3
        assert seen_doi_exists(conn, "10.1/a", topic_id="gfcf")
        assert seen_doi_exists(conn, "10.2/b", topic_id="gfcf")
        assert seen_doi_exists(conn, "10.3/c", topic_id="gfcf")

    def test_deduplicates(self, conn, tmp_path):
        state = {"seen_dois": ["10.1/a", "10.1/a", "10.2/b"]}
        json_file = tmp_path / "state.json"
        json_file.write_text(json.dumps(state))

        count = migrate_from_json(conn, json_file)
        assert count == 2

    def test_missing_file(self, conn, tmp_path):
        count = migrate_from_json(conn, tmp_path / "nope.json")
        assert count == 0

    def test_no_row_limit(self, conn, tmp_path):
        """Verify that unlike legacy FIFO (10000), SQLite can hold >10000 DOIs."""
        dois = [f"10.{i}/paper" for i in range(12000)]
        state = {"seen_dois": dois}
        json_file = tmp_path / "big_state.json"
        json_file.write_text(json.dumps(state))

        count = migrate_from_json(conn, json_file)
        assert count == 12000
        stats = get_dedup_stats(conn)
        assert stats["__global__"] == 12000
        # Verify spot check
        assert seen_doi_exists(conn, "10.5000/paper")
        assert seen_doi_exists(conn, "10.11999/paper")
