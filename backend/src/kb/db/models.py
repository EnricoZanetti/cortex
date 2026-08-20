"""SQLAlchemy models for the metadata store.

Design notes
------------
* ``Document`` is the unit the user manages (upload / tag / delete). It carries the two
  dedup fingerprints: ``file_hash`` (raw bytes) and ``content_hash`` (normalised text).
* ``Chunk`` rows mirror the vectors stored in Qdrant. Keeping the text in Postgres gives
  us (a) a lexical index for hybrid retrieval, (b) neighbour lookup for
  ``fetch_chunk_context``, and (c) a source of truth if the vector store is rebuilt.
* ``Tag`` is a first-class table (not a string column) so ``list_tags`` can report exact
  document counts and the tag vocabulary stays a closed, enumerable set for the agent.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class DocumentStatus(enum.StrEnum):
    """Lifecycle of a document through the ingestion pipeline."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    #: Extracted text is identical to an already-indexed document (a re-export or a
    #: re-save of the same content). The row is kept so the upload is traceable in the
    #: UI, but it holds no chunks and no vectors, and it is invisible to retrieval.
    DUPLICATE = "duplicate"


#: Statuses that a retrieval query may see. Everything else is not searchable.
SEARCHABLE_STATUSES = (DocumentStatus.READY,)


class UserRole(enum.StrEnum):
    """A signed-up chat user, or the operator's own bootstrapped admin account."""

    USER = "user"
    ADMIN = "admin"


class User(Base):
    """A chat login: email/username/password plus a free-trial run counter.

    Admins bypass the counter entirely (see ``UserRepository.decrement_free_run`` and the
    ``/chat`` route) -- that role exists so the operator can hand out one login that always
    uses their own provider keys, for demoing the tool.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False),
        nullable=False,
        default=UserRole.USER,
        index=True,
    )
    free_runs_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),)


document_tags = Table(
    "document_tags",
    Base.metadata,
    Column(
        "document_id",
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        Integer,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Tag(Base):
    """A normalised (lowercase, trimmed) topic label attached to documents."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    documents: Mapped[list[Document]] = relationship(
        secondary=document_tags, back_populates="tags", lazy="selectin"
    )

    __table_args__ = (CheckConstraint("name = lower(name)", name="ck_tags_lowercase"),)


class Document(Base):
    """An uploaded source document plus its ingestion metadata."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)

    # Dedup fingerprints. file_hash is UNIQUE: the same bytes can only exist once.
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # content_hash is indexed but NOT unique: it detects re-exports of the same content
    # and lets the service layer decide (merge tags) rather than the database rejecting.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", native_enum=False),
        nullable=False,
        default=DocumentStatus.PENDING,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tags: Mapped[list[Tag]] = relationship(
        secondary=document_tags, back_populates="documents", lazy="selectin"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def tag_names(self) -> list[str]:
        """Tag names sorted alphabetically, for stable tool output."""
        return sorted(tag.name for tag in self.tags)


class Chunk(Base):
    """One embedded segment of a document.

    ``id`` is deterministic (see ``kb.ingestion.pipeline``): it is derived from the
    document id and the chunk content hash, so re-ingesting identical content upserts
    the same row and the same Qdrant point instead of creating duplicates.
    """

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Lexical half of hybrid retrieval. A Postgres GENERATED column keeps the index in
    # sync automatically -- no trigger, and no way for application code to forget it.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english'::regconfig, coalesce(heading_path, '') || ' ' || text)",
            persisted=True,
        ),
        nullable=True,
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )
