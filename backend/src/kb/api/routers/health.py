"""Health endpoint used by Docker healthchecks and the deployment platform."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from kb.api.deps import get_vector_store
from kb.api.schemas import HealthOut
from kb.db.models import Document
from kb.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthOut)
def healthz(response: Response, session: Session = Depends(get_db)) -> HealthOut:
    """Report readiness of Postgres and Qdrant. Returns 503 if either is unreachable."""
    database = "ok"
    vector_store = "ok"
    documents: int | None = None
    vectors: int | None = None

    try:
        session.execute(text("SELECT 1"))
        documents = int(session.scalar(select(func.count()).select_from(Document)) or 0)
    except Exception as exc:
        database = f"error: {exc}"

    try:
        store = get_vector_store()
        store.ensure_collection()
        vectors = store.count()
    except Exception as exc:
        vector_store = f"error: {exc}"

    healthy = database == "ok" and vector_store == "ok"
    if not healthy:
        response.status_code = 503
    return HealthOut(
        status="ok" if healthy else "degraded",
        database=database,
        vector_store=vector_store,
        documents=documents,
        vectors=vectors,
    )
