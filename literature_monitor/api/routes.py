"""HTTP routes for the manual literature search workbench."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import BackgroundTasks, HTTPException

from literature_monitor.api.schemas import SearchRunCreate
from literature_monitor.core.export import export_results
from literature_monitor.core.journals import list_journal_pools
from literature_monitor.core.search_service import (
    request_from_dict,
    run_search,
)
from literature_monitor.core.topics import list_search_topics
from literature_monitor.db import repositories as repo
from literature_monitor.db.connection import get_connection
from literature_monitor.db.schema import init_db


def register_routes(app):
    def _get_conn():
        with get_connection() as conn:
            init_db(conn)
            yield conn

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/journal-pools")
    def journal_pools():
        return {"pools": list_journal_pools()}

    @app.get("/api/search-topics")
    def search_topics():
        return {"topics": list_search_topics()}

    @app.post("/api/search-runs")
    def create_search_run(payload: SearchRunCreate, background_tasks: BackgroundTasks):
        dump = getattr(payload, "model_dump", None)
        data = dump() if dump else payload.__dict__
        request = request_from_dict(data)
        with get_connection() as conn:
            init_db(conn)
            search_run_id = repo.create_search_run(
                conn, asdict(request), request.date_from, request.date_to
            )
        background_tasks.add_task(run_search, search_run_id, request)
        return {
            "search_run_id": search_run_id,
            "status": "queued",
            "total_fetched": 0,
            "total_after_dedup": 0,
            "total_scored": 0,
            "error_message": None,
        }

    @app.get("/api/search-runs/{search_run_id}")
    def get_search_run(search_run_id: int):
        with get_connection() as conn:
            init_db(conn)
            row = repo.get_search_run(conn, search_run_id)
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        return row

    @app.get("/api/search-runs/{search_run_id}/results")
    def get_results(
        search_run_id: int,
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
    ):
        with get_connection() as conn:
            init_db(conn)
            total, rows = repo.list_results(
                conn,
                search_run_id,
                limit=limit,
                offset=offset,
                relevance=relevance,
                min_score=min_score,
                keyword=keyword,
                journal=journal,
                source=source,
                has_doi=has_doi,
                has_abstract=has_abstract,
                year=year,
                date_from=date_from,
                date_to=date_to,
                sort=sort,
            )
        return {"total": total, "results": rows}

    @app.get("/api/search-runs/{search_run_id}/results/{paper_id}")
    def get_result_detail(search_run_id: int, paper_id: int):
        with get_connection() as conn:
            init_db(conn)
            detail = repo.get_result_detail(conn, search_run_id, paper_id)
        if not detail:
            raise HTTPException(status_code=404, detail="not_found")
        return detail

    @app.get("/api/search-runs/{search_run_id}/export")
    def export_search_run(
        search_run_id: int,
        format: str = "csv",
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
    ):
        with get_connection() as conn:
            init_db(conn)
            _, rows = repo.list_results(
                conn,
                search_run_id,
                limit=10000,
                relevance=relevance,
                min_score=min_score,
                keyword=keyword,
                journal=journal,
                source=source,
                has_doi=has_doi,
                has_abstract=has_abstract,
                year=year,
                date_from=date_from,
                date_to=date_to,
                sort=sort,
            )
        try:
            media_type, content = export_results(rows, format)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            from fastapi.responses import Response
        except Exception:
            return content
        filename = f"search-run-{search_run_id}.{format}"
        return Response(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
