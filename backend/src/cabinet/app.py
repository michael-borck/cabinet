"""FastAPI application factory for the file-sorter backend."""

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cabinet.config import get_settings
from cabinet.db.engine import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="File Sorter", version="0.1.0", lifespan=lifespan)

    if settings.api_token:
        api_token = settings.api_token

        # Registered before CORS so CORS wraps it: preflights are answered
        # by CORSMiddleware and 401 responses still carry CORS headers.
        @app.middleware("http")
        async def require_api_token(request: Request, call_next):
            if request.url.path == "/api/v1/health" or request.method == "OPTIONS":
                return await call_next(request)
            supplied = request.headers.get("x-api-token", "")
            if not secrets.compare_digest(supplied, api_token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid API token."},
                )
            return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from cabinet.routers import (
        categories,
        files,
        jobs,
        sources,
    )
    from cabinet.routers import (
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
