"""FastAPI application factory."""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

    # Serve the React SPA build — must come last so /api/* routes take priority
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="spa")

    return app


app = create_app()
