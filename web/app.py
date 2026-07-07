"""FastAPI application factory."""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import ensure_app_dirs
from database.db import init_db
from web.routers import auth_router, jobs_router, chat_router, profile_router, ingest_router


def create_app() -> FastAPI:
    app = FastAPI(title="ART — Agentic Resume Tailoring", docs_url="/api/docs", redoc_url=None)

    # Per-user ChatAgent instances — keyed by user_id string
    app.state.chat_agents = {}

    @app.on_event("startup")
    def _startup() -> None:
        ensure_app_dirs()
        init_db()

    # CORS — only needed when running the Vite dev server alongside uvicorn locally
    if os.getenv("DEV_MODE"):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # API routes (all under /api/*)
    app.include_router(auth_router.router)
    app.include_router(jobs_router.router)
    app.include_router(chat_router.router)
    app.include_router(profile_router.router)
    app.include_router(ingest_router.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    # Serve the React SPA build — must come last so /api/* routes take priority.
    # Client-side routes (/login, /reset-password, …) must fall back to
    # index.html so direct loads and email links work, not just in-app navigation.
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

        # no-cache = always revalidate (it still allows conditional 304s). The
        # HTML must never be served stale or browsers keep an outdated bundle
        # whose API payloads the backend no longer accepts; hashed /assets
        # files are immutable and safe to cache.
        _revalidate = {"Cache-Control": "no-cache"}

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (static_dir / path).resolve()
            if path and candidate.is_relative_to(static_dir.resolve()) and candidate.is_file():
                return FileResponse(candidate, headers=_revalidate)
            return FileResponse(static_dir / "index.html", headers=_revalidate)

    return app


app = create_app()
