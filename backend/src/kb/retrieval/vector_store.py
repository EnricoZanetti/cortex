"""Qdrant vector store wrapper.

Why Qdrant: it is a purpose-built vector database with first-class *payload filtering*,
which is what makes ``search_by_tag`` and ``search_by_document`` correct rather than
approximate. Filtering inside the ANN search means the top-k we return is the true top-k
*within the filtered subset*, instead of the naive approach of over-fetching globally and
discarding non-matching results afterwards (which silently returns fewer, worse hits the
narrower the filter gets).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from kb.config import get_settings
from kb.logging import get_logger

logger = get_logger(__name__)

PAYLOAD_DOCUMENT_ID = "document_id"
PAYLOAD_TAGS = "tags"
PAYLOAD_CHUNK_INDEX = "chunk_index"


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """A chunk vector plus the payload needed for filtering."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    tags: list[str]
    vector: list[float]


@dataclass(frozen=True, slots=True)
class VectorHit:
    """A similarity search result."""

    chunk_id: uuid.UUID
    score: float


class VectorStore:
    """Thin, typed facade over the Qdrant collection used by the knowledge base."""

    def __init__(
        self,
        client: QdrantClient | None = None,
        *,
        collection: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        settings = get_settings()
        self.collection = collection or settings.qdrant_collection
        self.dimensions = dimensions or settings.embedding_dimensions
        self._client = client
        self._settings = settings

    @property
    def client(self) -> QdrantClient:
        """Connect lazily.

        Constructing a ``QdrantClient`` performs a version handshake, so building it
        eagerly would make merely *importing* the MCP server require a running Qdrant --
        and would make the tool-schema tests depend on infrastructure they do not use.
        """
        if self._client is None:
            self._client = QdrantClient(
                url=self._settings.qdrant_url,
                api_key=(
                    self._settings.qdrant_api_key.get_secret_value()
                    if self._settings.qdrant_api_key
                    else None
                ),
                timeout=30,
            )
        return self._client

    # --- schema --------------------------------------------------------------------

    def ensure_collection(self) -> None:
        """Create the collection and payload indexes if they do not exist. Idempotent."""
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=self.dimensions, distance=qmodels.Distance.COSINE
                ),
            )
            logger.info("qdrant_collection_created", collection=self.collection)
        # Payload indexes make filtered search fast; creating an existing one is a no-op
        # server-side but raises on some versions, hence the guard.
        for field, schema in (
            (PAYLOAD_DOCUMENT_ID, qmodels.PayloadSchemaType.KEYWORD),
            (PAYLOAD_TAGS, qmodels.PayloadSchemaType.KEYWORD),
            (PAYLOAD_CHUNK_INDEX, qmodels.PayloadSchemaType.INTEGER),
        ):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection, field_name=field, field_schema=schema
                )
            except Exception as exc:  # pragma: no cover - already-exists is benign
                logger.debug("qdrant_index_exists", field=field, error=str(exc))

    # --- writes --------------------------------------------------------------------

    def upsert(self, points: list[VectorPoint]) -> None:
        """Insert or replace chunk vectors.

        Point ids are the deterministic chunk ids, so re-ingesting a document overwrites
        in place: duplicate vectors are structurally impossible, not merely unlikely.
        """
        if not points:
            return
        self.client.upsert(
            collection_name=self.collection,
            points=[
                qmodels.PointStruct(
                    id=str(point.chunk_id),
                    vector=point.vector,
                    payload={
                        PAYLOAD_DOCUMENT_ID: str(point.document_id),
                        PAYLOAD_CHUNK_INDEX: point.chunk_index,
                        PAYLOAD_TAGS: point.tags,
                    },
                )
                for point in points
            ],
            wait=True,
        )

    def delete_document(self, document_id: uuid.UUID) -> None:
        """Remove every vector belonging to a document (used on delete and re-ingest)."""
        self.client.delete(
            collection_name=self.collection,
            points_selector=qmodels.FilterSelector(filter=self._document_filter([document_id])),
            wait=True,
        )

    def update_tags(self, document_id: uuid.UUID, tags: list[str]) -> None:
        """Keep the vector payload in sync after a tag change on an existing document."""
        self.client.set_payload(
            collection_name=self.collection,
            payload={PAYLOAD_TAGS: tags},
            points=qmodels.FilterSelector(filter=self._document_filter([document_id])).filter,
            wait=True,
        )

    # --- reads ---------------------------------------------------------------------

    def search(
        self,
        vector: list[float],
        *,
        limit: int,
        document_ids: list[uuid.UUID] | None = None,
        tags: list[str] | None = None,
        tag_match: str = "any",
        score_threshold: float | None = None,
    ) -> list[VectorHit]:
        """Similarity search with optional pre-filtering on document ids and/or tags."""
        query_filter = self._build_filter(document_ids=document_ids, tags=tags, tag_match=tag_match)
        if query_filter is not None and not query_filter.must:
            return []
        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            score_threshold=score_threshold,
            with_payload=False,
        )
        return [
            VectorHit(chunk_id=uuid.UUID(str(point.id)), score=float(point.score))
            for point in response.points
        ]

    def count(self, document_id: uuid.UUID | None = None) -> int:
        """Number of stored vectors, optionally scoped to one document."""
        result = self.client.count(
            collection_name=self.collection,
            count_filter=self._document_filter([document_id]) if document_id else None,
            exact=True,
        )
        return int(result.count)

    # --- filter helpers ------------------------------------------------------------

    @staticmethod
    def _document_filter(document_ids: list[uuid.UUID]) -> qmodels.Filter:
        return qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key=PAYLOAD_DOCUMENT_ID,
                    match=qmodels.MatchAny(any=[str(value) for value in document_ids]),
                )
            ]
        )

    @staticmethod
    def _build_filter(
        *,
        document_ids: list[uuid.UUID] | None,
        tags: list[str] | None,
        tag_match: str,
    ) -> qmodels.Filter | None:
        conditions: list[qmodels.Condition] = []
        if document_ids is not None:
            if not document_ids:
                # An empty allow-list must match nothing, not everything.
                return qmodels.Filter(must=[])
            conditions.append(
                qmodels.FieldCondition(
                    key=PAYLOAD_DOCUMENT_ID,
                    match=qmodels.MatchAny(any=[str(value) for value in document_ids]),
                )
            )
        if tags:
            if tag_match == "all":
                conditions.extend(
                    qmodels.FieldCondition(key=PAYLOAD_TAGS, match=qmodels.MatchValue(value=tag))
                    for tag in tags
                )
            else:
                conditions.append(
                    qmodels.FieldCondition(key=PAYLOAD_TAGS, match=qmodels.MatchAny(any=tags))
                )
        return qmodels.Filter(must=conditions) if conditions else None
