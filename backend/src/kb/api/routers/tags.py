"""Tag vocabulary endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from kb.api.schemas import TagOut
from kb.db.repositories import TagRepository
from kb.db.session import get_db

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(
    include_pending: bool = Query(
        default=True,
        description="Include tags whose documents are still being ingested (UI wants these).",
    ),
    session: Session = Depends(get_db),
) -> list[TagOut]:
    """List every tag attached to at least one document, with document counts."""
    counts = TagRepository(session).list_with_counts(ready_only=not include_pending)
    return [TagOut(name=item.name, document_count=item.document_count) for item in counts]
