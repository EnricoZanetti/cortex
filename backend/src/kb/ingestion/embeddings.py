"""Embedding providers.

The pipeline depends on the ``Embedder`` protocol, never on OpenAI directly, so a local
model (e.g. a sentence-transformers server) can be swapped in by implementing two methods
and changing one line of wiring. Tests use ``FakeEmbedder`` for the same reason.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from kb.config import get_settings
from kb.logging import get_logger

logger = get_logger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced."""


@runtime_checkable
class Embedder(Protocol):
    """Structural interface for anything that can turn text into vectors."""

    @property
    def dimensions(self) -> int: ...

    @property
    def model(self) -> str: ...

    @property
    def is_semantic(self) -> bool:
        """False for stand-in embedders whose vectors carry no meaning.

        Retrieval consults this: fusing a meaningless ranking into the results is worse
        than having one retriever, because RRF would promote noise over genuine lexical
        matches. A ``False`` here makes the hybrid search degrade cleanly to keyword-only.
        """
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    """OpenAI embeddings.

    ``text-embedding-3-small`` (1536-d) is the default: it is roughly 5x cheaper than
    ``-large`` at close to the same retrieval quality on prose like policies and manuals,
    which means the whole corpus can be re-embedded on a whim when the chunker changes.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.embedding_model
        self._dimensions = dimensions or settings.embedding_dimensions
        self._batch_size = batch_size or settings.embedding_batch_size
        self._client = OpenAI(api_key=api_key or settings.openai_api_key.get_secret_value())

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_semantic(self) -> bool:
        return True

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts, batched. Order of the result matches the input."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        if not text.strip():
            raise EmbeddingError("Cannot embed an empty query.")
        return self._embed_batch([text])[0]

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=[text if text.strip() else " " for text in batch],
                dimensions=self._dimensions,
            )
        except Exception as exc:
            logger.warning("embedding_request_failed", model=self._model, error=str(exc))
            raise
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


class HashEmbedder:
    """Deterministic hash-based embedder for tests, CI and offline demos.

    It is not semantically meaningful -- with it, retrieval quality comes entirely from
    the lexical half of the hybrid search. It exists so the whole stack (ingestion, MCP
    tools, frontend) can be run and tested without an API key or network access, which
    keeps CI hermetic and lets a reviewer boot the project before adding credentials.
    """

    def __init__(self, dimensions: int = 1536) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model(self) -> str:
        return "hash-embedder"

    @property
    def is_semantic(self) -> bool:
        return False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        import hashlib
        import math

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [
            (digest[index % len(digest)] + index) % 251 / 251.0 for index in range(self._dimensions)
        ]
        norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        return [value / norm for value in raw]


def build_embedder() -> Embedder:
    """Factory used by every application entrypoint, driven by ``EMBEDDING_PROVIDER``."""
    settings = get_settings()
    if settings.embedding_provider == "hash":
        logger.warning(
            "using_hash_embedder",
            detail="Offline mode: semantic retrieval is disabled, lexical search only.",
        )
        return HashEmbedder(dimensions=settings.embedding_dimensions)
    return OpenAIEmbedder()
