"""FastAPI app entrypoint."""

from __future__ import annotations

from literature_monitor.compat import WEB_DIR
from literature_monitor.db.connection import connect
from literature_monitor.db.schema import init_db


def create_app():
    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "FastAPI is required for the web app. Install with `pip install -e .`."
        ) from exc

    from literature_monitor.api.routes import register_routes

    conn = connect()
    init_db(conn)

    app = FastAPI(title="Weekly Literature Monitor Workbench")
    register_routes(app)

    static_dir = WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()

