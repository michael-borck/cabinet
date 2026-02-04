"""FastAPI application factory for the file-sorter backend."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from file_sorter.config import get_settings
from file_sorter.db.engine import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="File Sorter", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from file_sorter.routers import (
        categories,
        files,
        jobs,
        sources,
    )
    from file_sorter.routers import (
        settings as settings_router,
    )

    app.include_router(sources.router, prefix="/api/v1")
    app.include_router(files.router, prefix="/api/v1")
    app.include_router(categories.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(settings_router.router, prefix="/api/v1")

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    return app
