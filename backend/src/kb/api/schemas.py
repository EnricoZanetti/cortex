"""Pydantic schemas for the management REST API (consumed by the frontend)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from kb.db.models import Document, DocumentStatus


class TagOut(BaseModel):
    """A tag with the number of documents carrying it."""

    name: str
    document_count: int


class DocumentOut(BaseModel):
    """A document as shown in the management UI."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    title: str | None
    tags: list[str]
    status: DocumentStatus
    chunk_count: int
    page_count: int | None
    byte_size: int
    content_type: str
    summary: str | None
    error: str | None
    duplicate_of: uuid.UUID | None = Field(
        default=None,
        description="Set when this upload duplicates the content of another document.",
    )
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def from_document(cls, document: Document) -> DocumentOut:
        return cls(
            id=document.id,
            filename=document.filename,
            title=document.title,
            tags=document.tag_names,
            status=document.status,
            chunk_count=document.chunk_count,
            page_count=document.page_count,
            byte_size=document.byte_size,
            content_type=document.content_type,
            summary=document.summary,
            error=document.error,
            duplicate_of=document.duplicate_of_id,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DocumentListOut(BaseModel):
    """A page of documents."""

    documents: list[DocumentOut]
    total: int
    limit: int
    offset: int


class UploadOut(BaseModel):
    """Result of an upload, including the deduplication verdict."""

    document: DocumentOut
    duplicate: bool = Field(description="True when this file was already in the knowledge base.")
    tags_added: list[str] = Field(default_factory=list)
    message: str


class DeleteOut(BaseModel):
    """Confirmation of a delete."""

    deleted: bool
    document_id: uuid.UUID


class HealthOut(BaseModel):
    """Liveness/readiness of the service and its dependencies."""

    status: str
    database: str
    vector_store: str
    documents: int | None = None
    vectors: int | None = None


class ErrorOut(BaseModel):
    """Uniform error envelope."""

    error: str
    detail: str | None = None
