"""FastAPI application for document management.

This is the *management plane* (upload, tag, list, delete) consumed by the web UI. The
*query plane* consumed by AI agents is the separate MCP server in ``kb.mcp_server``. They
share the same core package, so retrieval and ingestion logic exists exactly once.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kb.api.deps import get_vector_store
from kb.api.routers import documents, health, tags
from kb.api.schemas import ErrorOut
from kb.config import get_settings
from kb.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ensure the vector collection exists before serving traffic."""
    configure_logging()
    try:
        get_vector_store().ensure_collection()
    except Exception as exc:  # pragma: no cover - startup ordering in compose
        logger.warning("vector_store_unavailable_at_startup", error=str(exc))
    logger.info("api_started")
    yield


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    app = FastAPI(
        title="Document Intelligence — Management API",
        description=(
            "Upload, tag, list and delete documents in the knowledge base. "
            "AI agents query the same knowledge base through the MCP server."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(tags.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Return a uniform error envelope instead of leaking a stack trace."""
        logger.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content=ErrorOut(
                error="internal_error", detail="An unexpected error occurred."
            ).model_dump(),
        )

    return app


app = create_app()
