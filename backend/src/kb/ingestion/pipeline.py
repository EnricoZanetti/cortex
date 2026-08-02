"""The ingestion pipeline: bytes in, searchable chunks out.

Flow
----
``upload`` (fast, synchronous): hash -> dedup decision -> persist file + metadata row.
``process`` (slow, background): parse -> chunk -> embed -> upsert vectors -> write chunks.

Splitting it this way means the HTTP upload returns immediately with a document id the UI
can poll, and a slow OpenAI call can never time out a user request.

Deduplication is enforced at three levels, so "re-uploading a document must not create
duplicates" holds regardless of *how* the document is re-uploaded:

1. ``file_hash``    - SHA-256 of the raw bytes, UNIQUE in Postgres. The same file is the
                      same document, full stop. New tags on re-upload are merged in.
2. ``content_hash`` - SHA-256 of the normalised extracted text. Catches the same document
                      re-exported or re-saved (different bytes, identical content).
3. chunk ids        - deterministic UUIDv5 of ``(document_id, chunk content)``, reused as
                      the Qdrant point id, so re-processing upserts in place.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from kb.config import get_settings
from kb.db.models import Chunk, Document, DocumentStatus
from kb.db.repositories import DocumentRepository, TagRepository, normalize_tag
from kb.db.session import session_scope
from kb.ingestion.chunking import Chunker, TextChunk
from kb.ingestion.embeddings import Embedder, build_embedder
from kb.ingestion.parsers import (
    DocumentParseError,
    ParsedDocument,
    UnsupportedDocumentError,
    parse_document,
    resolve_parser_kind,
)
from kb.logging import get_logger
from kb.retrieval.vector_store import VectorPoint, VectorStore

logger = get_logger(__name__)

# Namespace for deterministic chunk ids. Fixed forever: changing it would orphan vectors.
CHUNK_NAMESPACE = uuid.UUID("6f4d6f1e-1a7b-4d5f-9a6c-9d4a2b3c1e70")

_WHITESPACE = re.compile(r"\s+")


class IngestionError(RuntimeError):
    """Raised when a document cannot be ingested."""


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Outcome of an upload request."""

    document: Document
    duplicate: bool
    duplicate_reason: str | None
    tags_added: list[str]

    @property
    def message(self) -> str:
        if not self.duplicate:
            return "Document accepted and queued for processing."
        base = (
            "This file is already in the knowledge base"
            if self.duplicate_reason == "file_hash"
            else "A document with identical content is already in the knowledge base"
        )
        if self.tags_added:
            return f"{base}; added new tag(s): {', '.join(self.tags_added)}."
        return f"{base}; no changes made."


def file_fingerprint(data: bytes) -> str:
    """SHA-256 over the raw bytes."""
    return hashlib.sha256(data).hexdigest()


def content_fingerprint(text: str) -> str:
    """SHA-256 over whitespace-normalised, lowercased text.

    Normalising before hashing is what lets us detect the same policy re-exported from
    Word with different line breaks or a regenerated PDF timestamp.
    """
    return hashlib.sha256(_WHITESPACE.sub(" ", text).strip().lower().encode("utf-8")).hexdigest()


def chunk_id_for(document_id: uuid.UUID, chunk_content_hash: str) -> uuid.UUID:
    """Deterministic chunk id: same document + same content => same id => upsert."""
    return uuid.uuid5(CHUNK_NAMESPACE, f"{document_id}:{chunk_content_hash}")


class IngestionService:
    """Coordinates parsing, chunking, embedding and storage."""

    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        vector_store: VectorStore | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._chunker = chunker

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = build_embedder()
        return self._embedder

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            self._vector_store = VectorStore()
        return self._vector_store

    @property
    def chunker(self) -> Chunker:
        if self._chunker is None:
            self._chunker = Chunker()
        return self._chunker

    # --- step 1: upload --------------------------------------------------------------

    def upload(
        self,
        session: Session,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        tags: list[str],
    ) -> UploadResult:
        """Validate, deduplicate and register an uploaded file.

        Never raises on a duplicate: re-uploading is a legitimate user action, so it
        returns the existing document and merges any tags the user added this time.
        """
        settings = get_settings()
        if not data:
            raise IngestionError("The uploaded file is empty.")
        if len(data) > settings.max_upload_bytes:
            raise IngestionError(
                f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB upload limit."
            )
        try:
            resolve_parser_kind(filename, content_type)
        except UnsupportedDocumentError as exc:
            raise IngestionError(str(exc)) from exc

        documents = DocumentRepository(session)
        tag_repo = TagRepository(session)
        requested_tags = sorted({normalize_tag(tag) for tag in tags if normalize_tag(tag)})

        file_hash = file_fingerprint(data)
        existing = documents.get_by_file_hash(file_hash)
        if existing is not None:
            added = self._merge_tags(session, existing, requested_tags)
            return UploadResult(
                document=existing, duplicate=True, duplicate_reason="file_hash", tags_added=added
            )

        storage_path = self._persist(file_hash, filename, data)
        document = Document(
            id=uuid.uuid4(),
            filename=filename,
            content_type=content_type or "application/octet-stream",
            byte_size=len(data),
            file_hash=file_hash,
            status=DocumentStatus.PENDING,
            storage_path=str(storage_path),
            tags=tag_repo.get_or_create_many(requested_tags),
        )
        documents.add(document)
        logger.info("document_uploaded", document_id=str(document.id), filename=filename)
        return UploadResult(
            document=document, duplicate=False, duplicate_reason=None, tags_added=requested_tags
        )

    def _merge_tags(self, session: Session, document: Document, tags: list[str]) -> list[str]:
        """Add tags not already present. Keeps Qdrant payloads in sync."""
        existing = set(document.tag_names)
        additions = [tag for tag in tags if tag not in existing]
        if not additions:
            return []
        document.tags = TagRepository(session).get_or_create_many(sorted(existing | set(tags)))
        session.flush()
        if document.status == DocumentStatus.READY:
            try:
                self.vector_store.update_tags(document.id, document.tag_names)
            except Exception as exc:  # pragma: no cover - vector store availability
                logger.warning("tag_sync_failed", document_id=str(document.id), error=str(exc))
        logger.info("document_tags_merged", document_id=str(document.id), added=additions)
        return additions

    @staticmethod
    def _persist(file_hash: str, filename: str, data: bytes) -> Path:
        """Store the original file, keyed by content hash (so identical files share one blob)."""
        directory = Path(get_settings().storage_dir)
        directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower()
        path = directory / f"{file_hash}{suffix}"
        if not path.exists():
            path.write_bytes(data)
        return path

    # --- step 2: process -------------------------------------------------------------

    def process_document(self, document_id: uuid.UUID) -> None:
        """Run the slow half of the pipeline. Safe to call repeatedly (idempotent).

        Opens its own session because it runs as a background task, decoupled from the
        request that created the document.
        """
        with session_scope() as session:
            document = DocumentRepository(session).get(document_id)
            if document is None:
                logger.warning("process_missing_document", document_id=str(document_id))
                return
            document.status = DocumentStatus.PROCESSING
            document.error = None
            session.flush()
            storage_path = document.storage_path
            filename = document.filename
            content_type = document.content_type

        try:
            data = Path(storage_path).read_bytes() if storage_path else b""
            if not data:
                raise IngestionError("Stored file is missing or empty; re-upload required.")
            self._ingest(document_id, data, filename, content_type)
        except Exception as exc:
            logger.exception("ingestion_failed", document_id=str(document_id))
            self._mark_failed(document_id, exc)

    def _ingest(
        self, document_id: uuid.UUID, data: bytes, filename: str, content_type: str
    ) -> None:
        parsed = parse_document(data, filename, content_type)
        text_chunks = self.chunker.chunk(parsed)
        if not text_chunks:
            raise DocumentParseError(f"{filename!r} produced no chunks.")

        vectors = self.embedder.embed_documents([chunk.embedding_text for chunk in text_chunks])
        if len(vectors) != len(text_chunks):
            raise IngestionError("Embedding provider returned a mismatched number of vectors.")

        with session_scope() as session:
            repo = DocumentRepository(session)
            document = repo.get(document_id)
            if document is None:
                raise IngestionError("Document disappeared during processing.")

            # Dedup level 2: the same content re-exported or re-saved arrives with
            # different bytes, so file_hash misses it. Here the extracted text is
            # available, and an identical fingerprint means indexing this document again
            # would return the same passage twice in every search.
            content_hash = content_fingerprint(parsed.text)
            twin = repo.get_by_content_hash(content_hash)
            if twin is not None and twin.id != document.id and twin.status == DocumentStatus.READY:
                self._fold_into_twin(session, document, twin, content_hash)
                logger.info(
                    "content_duplicate_folded",
                    document_id=str(document.id),
                    duplicate_of=str(twin.id),
                )
                return

            rows, points = self._build_rows(document, text_chunks, vectors)

            # Replace vectors before rows so a crash leaves the DB authoritative.
            self.vector_store.ensure_collection()
            self.vector_store.delete_document(document.id)
            self.vector_store.upsert(points)

            repo.replace_chunks(document.id, rows)
            document.content_hash = content_hash
            document.title = parsed.title or document.title
            document.page_count = parsed.page_count
            document.chunk_count = len(rows)
            document.char_count = len(parsed.text)
            document.summary = self._build_summary(parsed, text_chunks)
            document.status = DocumentStatus.READY
            document.error = None
            session.flush()

        logger.info("document_ingested", document_id=str(document_id), chunks=len(text_chunks))

    def _fold_into_twin(
        self, session: Session, document: Document, twin: Document, content_hash: str
    ) -> None:
        """Record a content-level duplicate against the document that already holds it.

        The upload row is kept (so the UI can explain what happened to the file the user
        just uploaded) but carries no chunks and no vectors, and any tags the user added
        this time are merged into the canonical document so no metadata is lost.
        """
        self._merge_tags(session, twin, document.tag_names)
        document.status = DocumentStatus.DUPLICATE
        document.duplicate_of_id = twin.id
        document.content_hash = content_hash
        document.chunk_count = 0
        document.error = None
        document.summary = f"Identical content to {twin.filename}; not indexed separately."
        session.flush()

    @staticmethod
    def _build_rows(
        document: Document,
        text_chunks: list[TextChunk],
        vectors: list[list[float]],
    ) -> tuple[list[Chunk], list[VectorPoint]]:
        """Build ORM rows and Qdrant points that share the same deterministic chunk ids.

        Two chunks of one document can hash identically (boilerplate headers/footers);
        the id would then collide, so such repeats are dropped -- storing one vector for
        identical text is the correct behaviour anyway.
        """
        tag_names = document.tag_names
        rows: list[Chunk] = []
        points: list[VectorPoint] = []
        seen: set[uuid.UUID] = set()

        position = 0
        for chunk, vector in zip(text_chunks, vectors, strict=True):
            content_hash = hashlib.sha256(chunk.embedding_text.encode("utf-8")).hexdigest()
            chunk_id = chunk_id_for(document.id, content_hash)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            rows.append(
                Chunk(
                    id=chunk_id,
                    document_id=document.id,
                    chunk_index=position,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    page=chunk.page,
                    token_count=chunk.token_count,
                    content_hash=content_hash,
                )
            )
            points.append(
                VectorPoint(
                    chunk_id=chunk_id,
                    document_id=document.id,
                    chunk_index=position,
                    tags=tag_names,
                    vector=vector,
                )
            )
            position += 1
        return rows, points

    @staticmethod
    def _build_summary(parsed: ParsedDocument, text_chunks: list[TextChunk]) -> str:
        """Extractive summary: the opening prose of the document, trimmed to ~600 chars.

        Deliberately not an LLM call -- ``get_document_summary`` is an orientation tool,
        and an extractive lead paragraph is faithful, free and instant. An abstractive
        summary is listed in the README as a future improvement.
        """
        for chunk in text_chunks:
            body = " ".join(chunk.text.split())
            if len(body) >= 120:
                return body[:600] + ("..." if len(body) > 600 else "")
        head = " ".join(parsed.text.split())
        return head[:600] + ("..." if len(head) > 600 else "")

    @staticmethod
    def _mark_failed(document_id: uuid.UUID, exc: Exception) -> None:
        with session_scope() as session:
            document = DocumentRepository(session).get(document_id)
            if document is None:
                return
            document.status = DocumentStatus.FAILED
            document.error = f"{type(exc).__name__}: {exc}"[:2000]
            document.chunk_count = 0
            session.flush()

    # --- deletion ---------------------------------------------------------------------

    def delete_document(self, session: Session, document: Document) -> None:
        """Delete a document everywhere: vectors first, then rows, then orphan tags."""
        document_id = document.id
        try:
            self.vector_store.delete_document(document_id)
        except Exception as exc:  # pragma: no cover - vector store availability
            logger.warning("vector_delete_failed", document_id=str(document_id), error=str(exc))
            raise IngestionError(
                "Could not remove vectors for this document; aborting delete to avoid "
                "orphaned embeddings."
            ) from exc
        DocumentRepository(session).delete(document)
        session.flush()
        TagRepository(session).prune_orphans()
        logger.info("document_deleted", document_id=str(document_id))
