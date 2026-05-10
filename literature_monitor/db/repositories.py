"""Repository layer for search runs, papers, scores, and exports."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from literature_monitor.core.dedup import normalize_title, normalized_doi
from literature_monitor.core.models import (
    Paper,
    PaperSourceRecord,
    ScoreResult,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def create_search_run(
    conn: sqlite3.Connection, query: dict[str, Any], date_from: str, date_to: str
) -> int:
    cur = conn.execute(
        """
        INSERT INTO search_runs(status, query_json, date_from, date_to)
        VALUES ('queued', ?, ?, ?)
        """,
        (_json(query), date_from, date_to),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_search_run(
    conn: sqlite3.Connection,
    search_run_id: int,
    *,
    status: str | None = None,
    error_message: str | None = None,
    total_fetched: int | None = None,
    total_after_dedup: int | None = None,
    total_scored: int | None = None,
    started: bool = False,
    completed: bool = False,
) -> None:
    parts = []
    values: list[Any] = []
    if status is not None:
        parts.append("status = ?")
        values.append(status)
    if error_message is not None:
        parts.append("error_message = ?")
        values.append(error_message)
    if total_fetched is not None:
        parts.append("total_fetched = ?")
        values.append(total_fetched)
    if total_after_dedup is not None:
        parts.append("total_after_dedup = ?")
        values.append(total_after_dedup)
    if total_scored is not None:
        parts.append("total_scored = ?")
        values.append(total_scored)
    if started:
        parts.append("started_at = CURRENT_TIMESTAMP")
    if completed:
        parts.append("completed_at = CURRENT_TIMESTAMP")
    if not parts:
        return
    values.append(search_run_id)
    conn.execute(f"UPDATE search_runs SET {', '.join(parts)} WHERE id = ?", values)
    conn.commit()


def get_search_run(
    conn: sqlite3.Connection, search_run_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM search_runs WHERE id = ?", (search_run_id,)
    ).fetchone()
    return dict(row) if row else None


def upsert_paper(conn: sqlite3.Connection, paper: Paper) -> int:
    doi_key = normalized_doi(paper.doi)
    title_key = normalize_title(paper.title)
    existing = None
    if doi_key:
        existing = conn.execute(
            "SELECT id FROM papers WHERE normalized_doi = ?", (doi_key,)
        ).fetchone()
    if not existing and title_key:
        existing = conn.execute(
            """
            SELECT id FROM papers
            WHERE normalized_title = ? AND COALESCE(year, '') = COALESCE(?, '')
            LIMIT 1
            """,
            (title_key, paper.year),
        ).fetchone()

    values = (
        paper.doi,
        doi_key,
        paper.title,
        title_key,
        paper.abstract,
        _json(paper.authors),
        paper.journal,
        paper.issn,
        paper.publisher,
        paper.year,
        paper.publication_date,
        paper.url,
        paper.oa_url,
        paper.citation_count,
        _json(paper.topics),
    )

    if existing:
        paper_id = int(existing["id"])
        existing_paper = conn.execute(
            "SELECT title FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        existing_title = existing_paper["title"] if existing_paper else ""
        should_update_title = len(paper.title) > len(existing_title or "")
        stored_title = paper.title if should_update_title else existing_title
        stored_title_key = normalize_title(stored_title)
        conn.execute(
            """
            UPDATE papers SET
              doi = COALESCE(?, doi),
              normalized_doi = COALESCE(?, normalized_doi),
              title = ?,
              normalized_title = ?,
              abstract = COALESCE(NULLIF(?, ''), abstract),
              authors_json = CASE WHEN ? != '[]' THEN ? ELSE authors_json END,
              journal = COALESCE(?, journal),
              issn = COALESCE(?, issn),
              publisher = COALESCE(?, publisher),
              year = COALESCE(?, year),
              publication_date = COALESCE(?, publication_date),
              url = COALESCE(?, url),
              oa_url = COALESCE(?, oa_url),
              citation_count = COALESCE(?, citation_count),
              topics_json = CASE WHEN ? != '[]' THEN ? ELSE topics_json END,
              updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                paper.doi,
                doi_key,
                stored_title,
                stored_title_key,
                paper.abstract,
                _json(paper.authors),
                _json(paper.authors),
                paper.journal,
                paper.issn,
                paper.publisher,
                paper.year,
                paper.publication_date,
                paper.url,
                paper.oa_url,
                paper.citation_count,
                _json(paper.topics),
                _json(paper.topics),
                paper_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO papers(
              doi, normalized_doi, title, normalized_title, abstract,
              authors_json, journal, issn, publisher, year, publication_date,
              url, oa_url, citation_count, topics_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        paper_id = int(cur.lastrowid)

    conn.execute(
        """
        INSERT OR REPLACE INTO paper_fts(rowid, title, abstract, journal, topics)
        SELECT id, title, COALESCE(abstract, ''), COALESCE(journal, ''), topics_json
        FROM papers WHERE id = ?
        """,
        (paper_id,),
    )
    conn.commit()
    return paper_id


def add_source_record(
    conn: sqlite3.Connection, paper_id: int, record: PaperSourceRecord
) -> None:
    conn.execute(
        """
        INSERT INTO paper_source_records(
          paper_id, source, source_work_id, doi, raw_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (paper_id, record.source, record.source_work_id, record.doi, _json(record.raw)),
    )
    conn.commit()


def add_score(
    conn: sqlite3.Connection,
    search_run_id: int,
    paper_id: int,
    score: ScoreResult,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO scored_results(
          search_run_id, paper_id, total_score, relevance_level, rule_score,
          text_score, recency_score, journal_score, matched_pipelines_json,
          matched_keywords_json, score_breakdown_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            search_run_id,
            paper_id,
            score.total_score,
            score.relevance_level,
            score.rule_score,
            score.text_score,
            score.recency_score,
            score.journal_score,
            _json(score.matched_pipelines),
            _json(score.matched_keywords),
            _json(score.breakdown),
        ),
    )
    conn.execute(
        "DELETE FROM keyword_hits WHERE search_run_id = ? AND paper_id = ?",
        (search_run_id, paper_id),
    )
    for hit in score.keyword_hits:
        conn.execute(
            """
            INSERT INTO keyword_hits(
              search_run_id, paper_id, keyword, field,
              hit_type, weight, snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                search_run_id,
                paper_id,
                hit.keyword,
                hit.field,
                hit.hit_type,
                hit.weight,
                hit.snippet,
            ),
        )
    conn.commit()


def list_results(
    conn: sqlite3.Connection,
    search_run_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
    relevance: str | None = None,
    min_score: float | None = None,
    keyword: str | None = None,
    journal: str | None = None,
    source: str | None = None,
    has_doi: bool | None = None,
    has_abstract: bool | None = None,
    year: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "score",
) -> tuple[int, list[dict[str, Any]]]:
    where = ["sr.search_run_id = ?"]
    params: list[Any] = [search_run_id]
    if relevance:
        where.append("sr.relevance_level = ?")
        params.append(relevance)
    if min_score is not None:
        where.append("sr.total_score >= ?")
        params.append(min_score)
    if keyword:
        where.append(
            """
            EXISTS (
              SELECT 1 FROM keyword_hits kh
              WHERE kh.search_run_id = sr.search_run_id
                AND kh.paper_id = sr.paper_id
                AND kh.keyword LIKE ?
            )
            """
        )
        params.append(f"%{keyword}%")
    if journal:
        where.append("p.journal LIKE ?")
        params.append(f"%{journal}%")
    if source:
        where.append(
            """
            EXISTS (
              SELECT 1 FROM paper_source_records ps
              WHERE ps.paper_id = p.id AND ps.source = ?
            )
            """
        )
        params.append(source)
    if has_doi is not None:
        if has_doi:
            where.append("p.doi IS NOT NULL AND p.doi != ''")
        else:
            where.append("(p.doi IS NULL OR p.doi = '')")
    if has_abstract is not None:
        where.append(
            "p.abstract IS NOT NULL AND p.abstract != ''"
            if has_abstract
            else "(p.abstract IS NULL OR p.abstract = '')"
        )
    if year:
        where.append("p.year = ?")
        params.append(year)
    if date_from:
        where.append("COALESCE(p.publication_date, p.year) >= ?")
        params.append(date_from)
    if date_to:
        where.append("COALESCE(p.publication_date, p.year) <= ?")
        params.append(date_to)

    order = "sr.total_score DESC, p.publication_date DESC"
    if sort == "date":
        order = "p.publication_date DESC, sr.total_score DESC"
    elif sort == "title":
        order = "p.title ASC"

    where_sql = " AND ".join(where)
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM scored_results sr"
        " JOIN papers p ON p.id = sr.paper_id"
        f" WHERE {where_sql}",
        params,
    ).fetchone()["c"]
    rows = conn.execute(
        f"""
        SELECT p.*, sr.total_score, sr.relevance_level, sr.rule_score, sr.text_score,
               sr.recency_score, sr.journal_score, sr.matched_pipelines_json,
               sr.matched_keywords_json, sr.score_breakdown_json
        FROM scored_results sr
        JOIN papers p ON p.id = sr.paper_id
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    return int(total), [_deserialize_result(dict(row)) for row in rows]


def get_result_detail(
    conn: sqlite3.Connection, search_run_id: int, paper_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT p.*, sr.total_score, sr.relevance_level, sr.rule_score, sr.text_score,
               sr.recency_score, sr.journal_score, sr.matched_pipelines_json,
               sr.matched_keywords_json, sr.score_breakdown_json
        FROM scored_results sr
        JOIN papers p ON p.id = sr.paper_id
        WHERE sr.search_run_id = ? AND sr.paper_id = ?
        """,
        (search_run_id, paper_id),
    ).fetchone()
    if not row:
        return None
    detail = _deserialize_result(dict(row))
    detail["keyword_hits"] = [
        dict(hit)
        for hit in conn.execute(
            """
            SELECT keyword, field, hit_type, weight, snippet
            FROM keyword_hits
            WHERE search_run_id = ? AND paper_id = ?
            ORDER BY weight DESC, field ASC
            """,
            (search_run_id, paper_id),
        ).fetchall()
    ]
    detail["source_records"] = [
        {
            **dict(source),
            "raw": json.loads(source["raw_json"]),
        }
        for source in conn.execute(
            """
            SELECT source, source_work_id, doi, raw_json, fetched_at
            FROM paper_source_records
            WHERE paper_id = ?
            ORDER BY source
            """,
            (paper_id,),
        ).fetchall()
    ]
    return detail


def _deserialize_result(row: dict[str, Any]) -> dict[str, Any]:
    list_keys = [
        "authors_json",
        "topics_json",
        "matched_pipelines_json",
        "matched_keywords_json",
    ]
    for key in list_keys:
        if key in row:
            row[key.removesuffix("_json")] = json.loads(row[key] or "[]")
            del row[key]
    if "score_breakdown_json" in row:
        row["score_breakdown"] = json.loads(row["score_breakdown_json"] or "{}")
        del row["score_breakdown_json"]
    return row
