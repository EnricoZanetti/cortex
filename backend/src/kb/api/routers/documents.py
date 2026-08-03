"""Document management endpoints used by the frontend."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.orm import Session

from kb.api.deps import get_ingestion_service
from kb.api.schemas import DeleteOut, DocumentListOut, DocumentOut, DocumentTagsIn, UploadOut
from kb.db.models import DocumentStatus
from kb.db.repositories import DocumentRepository
from kb.db.session import get_db
from kb.ingestion.pipeline import IngestionError
from kb.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _parse_tags(raw: list[str] | None) -> list[str]:
    """Accept tags as repeated form fields, a comma-separated string, or a JSON array.

    Browsers and HTTP clients disagree about how to send list-valued form fields; being
    permissive here removes a whole class of "my tags didn't stick" bug reports.
    """
    if not raw:
        return []
    tags: list[str] = []
    for item in raw:
        value = item.strip()
        if not value:
            continue
        if value.startswith("["):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                tags.extend(str(entry) for entry in parsed)
                continue
        tags.extend(part for part in value.split(",") if part.strip())
    return tags


@router.post("", response_model=UploadOut, status_code=201)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF, TXT or Markdown file.")],
    tags: Annotated[list[str] | None, Form(description="Topic tags for this document.")] = None,
    session: Session = Depends(get_db),
) -> UploadOut:
    """Upload a document and queue it for ingestion.

    Returns 201 with ``duplicate=false`` for a new document, or 200-style payload with
    ``duplicate=true`` when the same file is already present (tags are merged instead).
    """
    data = await file.read()
    service = get_ingestion_service()
    try:
        result = service.upload(
            session,
            data=data,
            filename=file.filename or "unnamed",
            content_type=file.content_type,
            tags=_parse_tags(tags),
        )
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not result.duplicate:
        # Commit before the background task starts, so it can see the row in its own session.
        session.commit()
        background_tasks.add_task(service.process_document, result.document.id)

    return UploadOut(
        document=DocumentOut.from_document(result.document),
        duplicate=result.duplicate,
        tags_added=result.tags_added,
        message=result.message,
    )


@router.get("", response_model=DocumentListOut)
def list_documents(
    tags: Annotated[list[str] | None, Query(description="Filter by tag.")] = None,
    tag_match: Literal["any", "all"] = "any",
    filename_contains: str | None = None,
    status: DocumentStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: Literal["recent", "name"] = "recent",
    session: Session = Depends(get_db),
) -> DocumentListOut:
    """List documents with their tags and ingestion status."""
    documents, total = DocumentRepository(session).list_documents(
        tags=_parse_tags(tags),
        tag_match=tag_match,
        filename_contains=filename_contains,
        statuses=[status] if status else None,
        limit=limit,
        offset=offset,
        sort=sort,
    )
    return DocumentListOut(
        documents=[DocumentOut.from_document(document) for document in documents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, session: Session = Depends(get_db)) -> DocumentOut:
    """Fetch a single document's metadata (used by the UI to poll ingestion status)."""
    document = DocumentRepository(session).get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return DocumentOut.from_document(document)


@router.post("/{document_id}/reprocess", response_model=DocumentOut)
def reprocess_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_db),
) -> DocumentOut:
    """Re-run ingestion for a document (after a failure, or a chunker change).

    Safe by construction: deterministic chunk ids mean re-processing upserts rather than
    duplicates.
    """
    repo = DocumentRepository(session)
    document = repo.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    document.status = DocumentStatus.PENDING
    document.error = None
    session.commit()
    background_tasks.add_task(get_ingestion_service().process_document, document_id)
    return DocumentOut.from_document(document)


@router.put("/{document_id}/tags", response_model=DocumentOut)
def update_document_tags(
    document_id: uuid.UUID,
    body: DocumentTagsIn,
    session: Session = Depends(get_db),
) -> DocumentOut:
    """Replace a document's tags outright (add and remove in one call)."""
    repo = DocumentRepository(session)
    document = repo.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    document = get_ingestion_service().set_tags(session, document, body.tags)
    session.commit()
    return DocumentOut.from_document(document)


@router.delete("/{document_id}", response_model=DeleteOut)
def delete_document(document_id: uuid.UUID, session: Session = Depends(get_db)) -> DeleteOut:
    """Delete a document, its chunks and its vectors."""
    repo = DocumentRepository(session)
    document = repo.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        get_ingestion_service().delete_document(session, document)
    except IngestionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return DeleteOut(deleted=True, document_id=document_id)
