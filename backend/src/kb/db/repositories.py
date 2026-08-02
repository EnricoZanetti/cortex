"""Data-access layer.

Every SQL statement in the application lives here. Services (ingestion, retrieval, MCP
tools) depend on these repositories rather than on SQLAlchemy directly, which keeps the
query surface small, testable and easy to audit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, Select, delete, func, select
from sqlalchemy.orm import Session

from kb.db.models import Chunk, Document, DocumentStatus, Tag, document_tags


def normalize_tag(raw: str) -> str:
    """Normalise a user-supplied tag to the canonical stored form.

    Lowercased, trimmed, inner whitespace collapsed to single hyphens. This is what makes
    the tag vocabulary a genuinely closed set: ``Compliance``, ``compliance `` and
    ``COMPLIANCE`` all address the same tag, so an agent cannot miss documents by casing.
    """
    collapsed = "-".join(raw.strip().lower().split())
    return collapsed.strip("-")[:64]


@dataclass(frozen=True)
class TagCount:
    """A tag together with the number of documents carrying it."""

    name: str
    document_count: int


class TagRepository:
    """Reads and writes on the tag vocabulary."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create_many(self, names: list[str]) -> list[Tag]:
        """Return ``Tag`` rows for ``names``, creating any that do not exist yet."""
        normalized = sorted({normalize_tag(name) for name in names if normalize_tag(name)})
        if not normalized:
            return []
        existing = list(self.session.scalars(select(Tag).where(Tag.name.in_(normalized))))
        missing = set(normalized) - {tag.name for tag in existing}
        for name in sorted(missing):
            tag = Tag(name=name)
            self.session.add(tag)
            existing.append(tag)
        self.session.flush()
        return sorted(existing, key=lambda tag: tag.name)

    def list_with_counts(self, *, ready_only: bool = True) -> list[TagCount]:
        """Return every tag attached to at least one document, with document counts."""
        stmt = (
            select(Tag.name, func.count(document_tags.c.document_id))
            .join(document_tags, document_tags.c.tag_id == Tag.id)
            .join(Document, Document.id == document_tags.c.document_id)
            .group_by(Tag.name)
            .order_by(func.count(document_tags.c.document_id).desc(), Tag.name)
        )
        if ready_only:
            stmt = stmt.where(Document.status == DocumentStatus.READY)
        return [
            TagCount(name=name, document_count=count) for name, count in self.session.execute(stmt)
        ]

    def existing_names(self, names: list[str]) -> set[str]:
        """Return the subset of ``names`` that exist in the vocabulary (normalised)."""
        normalized = [normalize_tag(name) for name in names]
        rows = self.session.scalars(select(Tag.name).where(Tag.name.in_(normalized)))
        return set(rows)

    def prune_orphans(self) -> int:
        """Delete tags no longer attached to any document. Returns the number removed."""
        orphan_ids = (
            select(Tag.id)
            .outerjoin(document_tags, document_tags.c.tag_id == Tag.id)
            .group_by(Tag.id)
            .having(func.count(document_tags.c.document_id) == 0)
        )
        # A DELETE always yields a CursorResult; the generic Result type Session.execute
        # is declared to return does not advertise .rowcount, so narrow it explicitly.
        result = cast(
            "CursorResult[Any]", self.session.execute(delete(Tag).where(Tag.id.in_(orphan_ids)))
        )
        return int(result.rowcount or 0)


class DocumentRepository:
    """Reads and writes on documents and their chunks."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- lookups -----------------------------------------------------------------

    def get(self, document_id: uuid.UUID) -> Document | None:
        return self.session.get(Document, document_id)

    def get_by_file_hash(self, file_hash: str) -> Document | None:
        return self.session.scalar(select(Document).where(Document.file_hash == file_hash))

    def get_by_content_hash(self, content_hash: str) -> Document | None:
        return self.session.scalar(
            select(Document)
            .where(Document.content_hash == content_hash)
            .order_by(Document.created_at)
            .limit(1)
        )

    def get_by_filename(self, filename: str) -> list[Document]:
        """Exact (case-insensitive) filename match."""
        stmt = select(Document).where(func.lower(Document.filename) == filename.strip().lower())
        return list(self.session.scalars(stmt))

    def search_by_filename_fragment(self, fragment: str, *, limit: int = 10) -> list[Document]:
        """Case-insensitive substring match on filename or title, for name resolution."""
        pattern = f"%{fragment.strip().lower()}%"
        stmt = (
            select(Document)
            .where(
                func.lower(Document.filename).like(pattern)
                | func.lower(func.coalesce(Document.title, "")).like(pattern)
            )
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def list_documents(
        self,
        *,
        tags: list[str] | None = None,
        tag_match: Literal["any", "all"] = "any",
        filename_contains: str | None = None,
        statuses: list[DocumentStatus] | None = None,
        limit: int = 20,
        offset: int = 0,
        sort: Literal["recent", "name"] = "recent",
    ) -> tuple[list[Document], int]:
        """Return a page of documents plus the total number of matches."""
        stmt = select(Document)
        stmt = self._apply_filters(stmt, tags, tag_match, filename_contains, statuses)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int(self.session.scalar(count_stmt) or 0)

        order = Document.created_at.desc() if sort == "recent" else func.lower(Document.filename)
        page = self.session.scalars(stmt.order_by(order).limit(limit).offset(offset))
        return list(page), total

    def ids_matching_tags(
        self, tags: list[str], tag_match: Literal["any", "all"] = "any"
    ) -> list[uuid.UUID]:
        """Return ids of READY documents matching the tag filter (used to scope search)."""
        stmt = select(Document.id).where(Document.status == DocumentStatus.READY)
        stmt = self._apply_tag_filter(stmt, tags, tag_match)
        return list(self.session.scalars(stmt))

    def _apply_filters(
        self,
        stmt: Select[tuple[Document]],
        tags: list[str] | None,
        tag_match: Literal["any", "all"],
        filename_contains: str | None,
        statuses: list[DocumentStatus] | None,
    ) -> Select[tuple[Document]]:
        if tags:
            stmt = self._apply_tag_filter(stmt, tags, tag_match)
        if filename_contains:
            pattern = f"%{filename_contains.strip().lower()}%"
            stmt = stmt.where(
                func.lower(Document.filename).like(pattern)
                | func.lower(func.coalesce(Document.title, "")).like(pattern)
            )
        if statuses:
            stmt = stmt.where(Document.status.in_(statuses))
        return stmt

    @staticmethod
    def _apply_tag_filter(
        stmt: Select[tuple],  # type: ignore[type-arg]
        tags: list[str],
        tag_match: Literal["any", "all"],
    ) -> Select[tuple]:  # type: ignore[type-arg]
        normalized = [normalize_tag(tag) for tag in tags if normalize_tag(tag)]
        if not normalized:
            return stmt
        subquery = (
            select(document_tags.c.document_id)
            .join(Tag, Tag.id == document_tags.c.tag_id)
            .where(Tag.name.in_(normalized))
            .group_by(document_tags.c.document_id)
        )
        if tag_match == "all":
            subquery = subquery.having(func.count(func.distinct(Tag.name)) == len(set(normalized)))
        return stmt.where(Document.id.in_(subquery))

    # --- writes ------------------------------------------------------------------

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def delete(self, document: Document) -> None:
        self.session.delete(document)

    def replace_chunks(self, document_id: uuid.UUID, chunks: list[Chunk]) -> None:
        """Atomically swap a document's chunk set (delete-then-insert).

        Used by ingestion and re-ingestion. Combined with deterministic chunk ids this
        guarantees a re-processed document can never accumulate stale or duplicate rows.
        """
        self.session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        self.session.add_all(chunks)
        self.session.flush()

    # --- chunk reads ---------------------------------------------------------------

    def get_chunk(self, chunk_id: uuid.UUID) -> Chunk | None:
        return self.session.get(Chunk, chunk_id)

    def get_chunks_by_ids(self, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, Chunk]:
        if not chunk_ids:
            return {}
        rows = self.session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))
        return {chunk.id: chunk for chunk in rows}

    def get_chunk_window(
        self, document_id: uuid.UUID, start_index: int, end_index: int
    ) -> list[Chunk]:
        """Return chunks of one document within an inclusive index range, in order."""
        stmt = (
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.chunk_index >= start_index,
                Chunk.chunk_index <= end_index,
            )
            .order_by(Chunk.chunk_index)
        )
        return list(self.session.scalars(stmt))

    def get_document_chunks(self, document_id: uuid.UUID) -> list[Chunk]:
        stmt = select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
        return list(self.session.scalars(stmt))

    def keyword_search(
        self,
        query: str,
        *,
        limit: int,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[tuple[uuid.UUID, float]]:
        """Lexical half of hybrid retrieval: Postgres full-text search over chunks.

        Returns ``(chunk_id, ts_rank)`` ordered best-first.

        The query is turned into an **OR** of its lexemes rather than the AND that
        ``websearch_to_tsquery``/``plainto_tsquery`` produce. That matters: an agent sends
        whole questions ("how quickly must I report a suspicious transaction?"), and no
        single passage contains every word of a question, so an AND query returns nothing
        at all and silently disables half of the hybrid search. With OR semantics,
        ``ts_rank_cd`` still ranks passages containing more of the query terms higher, so
        we get graceful degradation instead of a cliff.

        Lexemes are derived by Postgres itself (``to_tsvector`` -> ``tsvector_to_array``),
        which applies the same stemming and stop-word removal used to build the index and
        avoids hand-rolled, injection-prone query construction.
        """
        if not any(character.isalnum() for character in query):
            return []
        lexemes = func.tsvector_to_array(func.to_tsvector("english", query))
        tsquery = func.to_tsquery("english", func.array_to_string(lexemes, " | "))
        rank = func.ts_rank_cd(Chunk.search_vector, tsquery)
        stmt = (
            select(Chunk.id, rank)
            .where(Chunk.search_vector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit)
        )
        if document_ids is not None:
            if not document_ids:
                return []
            stmt = stmt.where(Chunk.document_id.in_(document_ids))
        return [(row[0], float(row[1])) for row in self.session.execute(stmt)]
