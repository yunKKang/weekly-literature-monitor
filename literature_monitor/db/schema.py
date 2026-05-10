"""Database schema for manual literature search."""

from __future__ import annotations

import sqlite3


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT NOT NULL,
          query_json TEXT NOT NULL,
          date_from TEXT NOT NULL,
          date_to TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          started_at TEXT,
          completed_at TEXT,
          error_message TEXT,
          total_fetched INTEGER NOT NULL DEFAULT 0,
          total_after_dedup INTEGER NOT NULL DEFAULT 0,
          total_scored INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS papers (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          doi TEXT,
          normalized_doi TEXT UNIQUE,
          title TEXT NOT NULL,
          normalized_title TEXT NOT NULL,
          abstract TEXT,
          authors_json TEXT NOT NULL DEFAULT '[]',
          journal TEXT,
          issn TEXT,
          publisher TEXT,
          year TEXT,
          publication_date TEXT,
          url TEXT,
          oa_url TEXT,
          citation_count INTEGER,
          topics_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS paper_source_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          source TEXT NOT NULL,
          source_work_id TEXT,
          doi TEXT,
          raw_json TEXT NOT NULL,
          fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scored_results (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
          paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          total_score REAL NOT NULL,
          relevance_level TEXT NOT NULL,
          rule_score REAL NOT NULL,
          text_score REAL NOT NULL,
          recency_score REAL NOT NULL,
          journal_score REAL NOT NULL,
          matched_pipelines_json TEXT NOT NULL DEFAULT '[]',
          matched_keywords_json TEXT NOT NULL DEFAULT '[]',
          score_breakdown_json TEXT NOT NULL DEFAULT '{}',
          UNIQUE(search_run_id, paper_id)
        );

        CREATE TABLE IF NOT EXISTS keyword_hits (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
          paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          keyword TEXT NOT NULL,
          field TEXT NOT NULL,
          hit_type TEXT NOT NULL,
          weight REAL NOT NULL DEFAULT 1,
          snippet TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5(
          title,
          abstract,
          journal,
          topics,
          content='papers',
          content_rowid='id'
        );

        -- Phase 3: Global DOI dedup table (replaces state/monitor_state.json)
        CREATE TABLE IF NOT EXISTS seen_dois (
          doi TEXT NOT NULL,
          topic_id TEXT NOT NULL DEFAULT '__global__',
          first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (doi, topic_id)
        );

        CREATE INDEX IF NOT EXISTS idx_seen_dois_topic
          ON seen_dois(topic_id);
        """
    )
    conn.commit()
