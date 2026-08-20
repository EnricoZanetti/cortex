"""Pydantic schemas for the management REST API (consumed by the frontend)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from kb.db.models import Document, DocumentStatus, User, UserRole


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


class DocumentTagsIn(BaseModel):
    """New tag set for a document; replaces the existing set entirely."""

    tags: list[str] = Field(default_factory=list)


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


class ChatModelOut(BaseModel):
    """One selectable chat model, with the reason it is unusable when it is."""

    id: str
    label: str
    provider: str
    provider_label: str
    description: str
    available: bool = Field(description="False when the provider's API key is not configured.")
    requires_env_var: str = Field(
        description="Environment variable that supplies this provider's API key."
    )


class ChatModelsOut(BaseModel):
    """The model picker's contents."""

    models: list[ChatModelOut]
    default: str | None = Field(
        default=None, description="Id to preselect; null when no provider is configured."
    )


class HealthOut(BaseModel):
    """Liveness/readiness of the service and its dependencies."""

    status: str
    database: str
    vector_store: str
    documents: int | None = None
    vectors: int | None = None
    chat_models_available: int = 0


class ErrorOut(BaseModel):
    """Uniform error envelope."""

    error: str
    detail: str | None = None


class ChatHealthOut(BaseModel):
    """Whether the chat assistant can reach the MCP server, and with which tools."""

    mcp_server: str
    mcp_server_url: str
    tools: list[str]
    models_available: int


class UserOut(BaseModel):
    """The logged-in user, and how much free trial usage they have left."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    username: str
    role: UserRole
    free_runs_remaining: int

    @classmethod
    def from_user(cls, user: User) -> UserOut:
        return cls(
            id=user.id,
            email=user.email,
            username=user.username,
            role=user.role,
            free_runs_remaining=user.free_runs_remaining,
        )


class AuthOut(BaseModel):
    """Response to signup/login: the bearer token plus the user it belongs to."""

    access_token: str
    token_type: str = "bearer"
    user: UserOut
