"""Shared FastAPI dependencies and process-wide service singletons."""

from __future__ import annotations

from functools import lru_cache

from kb.ingestion.embeddings import Embedder, build_embedder
from kb.ingestion.pipeline import IngestionService
from kb.retrieval.vector_store import VectorStore


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Process-wide embedder (holds an HTTP client, so it is worth reusing)."""
    return build_embedder()


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    """Process-wide Qdrant client."""
    return VectorStore()


@lru_cache(maxsize=1)
def get_ingestion_service() -> IngestionService:
    """Process-wide ingestion service wired to the shared embedder and vector store."""
    return IngestionService(embedder=get_embedder(), vector_store=get_vector_store())
