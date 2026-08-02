"""Retrieval service: one implementation behind all three search tools.

``search``, ``search_by_tag`` and ``search_by_document`` are distinct tools *for the
agent* -- distinct names and required parameters are a much stronger signal to an LLM
than one tool with optional filters it may forget to set -- but they are one code path
here, differing only in the filter passed in. That keeps ranking behaviour identical
across the three, which is what makes their shared result schema honest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from kb.config import get_settings
from kb.db.models import Chunk, Document
from kb.db.repositories import DocumentRepository
from kb.ingestion.embeddings import Embedder
from kb.logging import get_logger
from kb.retrieval.fusion import normalize_scores, reciprocal_rank_fusion
from kb.retrieval.vector_store import VectorHit, VectorStore

logger = get_logger(__name__)

TagMatch = Literal["any", "all"]


@dataclass(frozen=True, slots=True)
class SearchFilter:
    """The scoping applied to a search. Exactly one of the three tools sets each field."""

    tags: list[str] | None = None
    tag_match: TagMatch = "any"
    document_ids: list[uuid.UUID] | None = None

    @property
    def is_document_scoped(self) -> bool:
        return self.document_ids is not None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One retrieved chunk with everything an agent needs to cite or drill into it."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    document_title: str | None
    tags: list[str]
    chunk_index: int
    heading_path: str | None
    page: int | None
    text: str
    relevance: float
    #: Raw cosine similarity from the vector store, on an absolute 0..1 scale.
    #: ``None`` in offline mode, where no semantic retriever ran.
    similarity: float | None
    matched_by: list[str]
    truncated: bool


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """Full result set plus the diagnostics that help an agent recover from a bad query."""

    results: list[SearchResult]
    documents_searched: int
    total_candidates: int
    #: False when running with the offline stand-in embedder (keyword-only retrieval).
    semantic: bool = True

    @property
    def best_similarity(self) -> float | None:
        """Absolute similarity of the strongest hit, for judging whether *anything* fits.

        ``relevance`` is normalised against the best hit in the set, so the top result
        always scores 1.0 -- useful for comparing results to each other, useless for
        deciding whether the knowledge base covers the topic at all. This is the number
        that answers the second question.
        """
        scores = [result.similarity for result in self.results if result.similarity is not None]
        return max(scores) if scores else None


class RetrievalService:
    """Hybrid (dense + lexical) retrieval over the chunk store."""

    def __init__(
        self,
        session: Session,
        *,
        embedder: Embedder,
        vector_store: VectorStore,
    ) -> None:
        self.session = session
        self.embedder = embedder
        self.vector_store = vector_store
        self.documents = DocumentRepository(session)
        self.settings = get_settings()

    def search(
        self,
        query: str,
        *,
        scope: SearchFilter | None = None,
        top_k: int = 6,
        min_relevance: float = 0.0,
        max_chars_per_result: int = 800,
    ) -> SearchResponse:
        """Run a hybrid search and return hydrated, ranked, diversity-capped results."""
        scope = scope or SearchFilter()
        if not query.strip():
            return SearchResponse(results=[], documents_searched=0, total_candidates=0)

        document_ids = self._resolve_scope(scope)
        if document_ids is not None and not document_ids:
            return SearchResponse(results=[], documents_searched=0, total_candidates=0)

        pool = max(self.settings.retrieval_candidate_pool, top_k * 4)
        ranked: dict[str, list[uuid.UUID]] = {
            "keyword": self._keyword_search(query, document_ids, pool)
        }
        similarity: dict[uuid.UUID, float] = {}
        if self.embedder.is_semantic:
            dense = self._dense_search(query, scope, document_ids, pool)
            ranked["dense"] = [hit.chunk_id for hit in dense]
            similarity = {hit.chunk_id: hit.score for hit in dense}

        fused = reciprocal_rank_fusion(ranked, k=self.settings.rrf_k)
        scored = normalize_scores(fused)
        chunk_map = self.documents.get_chunks_by_ids([hit.chunk_id for hit, _ in scored])

        results: list[SearchResult] = []
        per_document: dict[uuid.UUID, int] = {}
        cap = len(chunk_map) if scope.is_document_scoped else self.settings.max_chunks_per_document

        for hit, relevance in scored:
            if len(results) >= top_k:
                break
            if relevance < min_relevance:
                continue
            chunk = chunk_map.get(hit.chunk_id)
            if chunk is None:
                # Vector present but row gone: a stale point. Skip and let the next
                # ingestion/delete cycle clean it up rather than failing the query.
                logger.warning("stale_vector", chunk_id=str(hit.chunk_id))
                continue
            if per_document.get(chunk.document_id, 0) >= cap:
                continue
            per_document[chunk.document_id] = per_document.get(chunk.document_id, 0) + 1
            results.append(
                self._to_result(
                    chunk,
                    relevance,
                    similarity.get(hit.chunk_id),
                    hit.retrievers,
                    max_chars_per_result,
                )
            )

        searched = (
            len(document_ids)
            if document_ids is not None
            else len({chunk.document_id for chunk in chunk_map.values()})
        )
        return SearchResponse(
            results=results,
            documents_searched=searched,
            total_candidates=len(scored),
            semantic=self.embedder.is_semantic,
        )

    # --- internals -------------------------------------------------------------------

    def _resolve_scope(self, scope: SearchFilter) -> list[uuid.UUID] | None:
        """Turn a filter into an explicit document-id allow-list (or ``None`` for global).

        Tag filters are resolved in Postgres rather than relying on the Qdrant payload
        alone, because the lexical retriever also needs the same allow-list -- resolving
        once keeps the two halves of the hybrid search perfectly consistent.
        """
        if scope.document_ids is not None:
            return list(scope.document_ids)
        if scope.tags:
            return self.documents.ids_matching_tags(scope.tags, scope.tag_match)
        return None

    def _dense_search(
        self,
        query: str,
        scope: SearchFilter,
        document_ids: list[uuid.UUID] | None,
        pool: int,
    ) -> list[VectorHit]:
        try:
            vector = self.embedder.embed_query(query)
        except Exception as exc:
            logger.warning("query_embedding_failed", error=str(exc))
            raise
        hits = self.vector_store.search(
            vector,
            limit=pool,
            document_ids=document_ids,
            tags=scope.tags if document_ids is None else None,
            tag_match=scope.tag_match,
        )
        return hits

    def _keyword_search(
        self, query: str, document_ids: list[uuid.UUID] | None, pool: int
    ) -> list[uuid.UUID]:
        try:
            rows = self.documents.keyword_search(query, limit=pool, document_ids=document_ids)
        except Exception as exc:  # pragma: no cover - malformed tsquery input
            logger.warning("keyword_search_failed", error=str(exc))
            return []
        return [chunk_id for chunk_id, _ in rows]

    @staticmethod
    def _to_result(
        chunk: Chunk,
        relevance: float,
        similarity: float | None,
        matched_by: list[str],
        max_chars: int,
    ) -> SearchResult:
        document: Document = chunk.document
        text = chunk.text
        truncated = len(text) > max_chars
        if truncated:
            # Cut on a word boundary so the agent never sees a mangled final token.
            text = text[:max_chars].rsplit(" ", 1)[0] + " ..."
        return SearchResult(
            chunk_id=chunk.id,
            document_id=document.id,
            filename=document.filename,
            document_title=document.title,
            tags=document.tag_names,
            chunk_index=chunk.chunk_index,
            heading_path=chunk.heading_path,
            page=chunk.page,
            text=text,
            relevance=relevance,
            similarity=similarity,
            matched_by=matched_by,
            truncated=truncated,
        )
