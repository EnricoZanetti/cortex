"""End-to-end tests against real Postgres and Qdrant.

Skipped automatically when those services are not reachable, so the default ``pytest``
run stays hermetic. Run them with the stack up:

    make up && cd backend && uv run pytest -m integration

Everything runs inside a dedicated Qdrant collection and is cleaned up afterwards, so
these tests never disturb a knowledge base you are demoing.
"""

from __future__ import annotations

import uuid

import pytest

from kb.db.models import DocumentStatus
from kb.db.repositories import DocumentRepository, TagRepository
from kb.db.session import session_scope
from kb.ingestion.embeddings import HashEmbedder
from kb.ingestion.pipeline import IngestionService
from kb.retrieval.service import RetrievalService, SearchFilter
from kb.retrieval.vector_store import VectorStore

pytestmark = pytest.mark.integration

POLICY = b"""\
# Travel Policy

## 1. Booking

Book flights at least fourteen days in advance through the corporate travel portal.

## 2. Limits

Any single trip above EUR 1500 requires department head approval before booking.

## 3. Expenses

Submit receipts within thirty days of returning from the trip.
"""


def services_available() -> bool:
    from sqlalchemy import text

    from kb.db.session import get_engine

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        VectorStore(collection="probe").client.get_collections()
    except Exception:
        return False
    return True


@pytest.fixture(scope="module")
def stack():
    if not services_available():
        pytest.skip("Postgres and/or Qdrant are not reachable")

    collection = f"test_{uuid.uuid4().hex[:8]}"
    store = VectorStore(collection=collection, dimensions=64)
    store.ensure_collection()
    embedder = HashEmbedder(dimensions=64)
    service = IngestionService(embedder=embedder, vector_store=store)
    created: list[uuid.UUID] = []

    yield service, store, embedder, created

    for document_id in created:
        with session_scope() as session:
            document = DocumentRepository(session).get(document_id)
            if document is not None:
                session.delete(document)
    store.client.delete_collection(collection)


def ingest(stack, data: bytes, filename: str, tags: list[str]):
    service, _, _, created = stack
    with session_scope() as session:
        result = service.upload(
            session, data=data, filename=filename, content_type="text/markdown", tags=tags
        )
        document_id = result.document.id
        duplicate = result.duplicate
        added = result.tags_added
    if not duplicate:
        created.append(document_id)
        service.process_document(document_id)
    return document_id, duplicate, added


def test_ingest_then_search_round_trip(stack) -> None:
    _, store, embedder, _ = stack
    document_id, duplicate, _ = ingest(
        stack, POLICY, f"travel_{uuid.uuid4().hex[:6]}.md", ["travel"]
    )
    assert not duplicate

    with session_scope() as session:
        document = DocumentRepository(session).get(document_id)
        assert document is not None
        assert document.status == DocumentStatus.READY
        assert document.chunk_count > 0
        assert store.count(document_id) == document.chunk_count

        retrieval = RetrievalService(session, embedder=embedder, vector_store=store)
        response = retrieval.search(
            "what approval is needed for an expensive trip?",
            scope=SearchFilter(document_ids=[document_id]),
            top_k=3,
        )

    assert response.results
    assert "1500" in " ".join(result.text for result in response.results)
    assert all(result.document_id == document_id for result in response.results)


def test_reupload_of_identical_bytes_creates_no_duplicate(stack) -> None:
    _, store, _, _ = stack
    filename = f"dedupe_{uuid.uuid4().hex[:6]}.md"
    document_id, _, _ = ingest(stack, POLICY, filename, ["travel"])
    vectors_before = store.count()

    same_id, duplicate, added = ingest(stack, POLICY, filename, ["travel", "finance"])

    assert duplicate is True
    assert same_id == document_id
    assert added == ["finance"], "new tags on a re-upload must be merged"
    assert store.count() == vectors_before, "a re-upload must not add vectors"


def test_reprocessing_is_idempotent(stack) -> None:
    service, store, _, _ = stack
    document_id, _, _ = ingest(stack, POLICY, f"reproc_{uuid.uuid4().hex[:6]}.md", ["travel"])
    vectors_before = store.count(document_id)

    service.process_document(document_id)

    assert store.count(document_id) == vectors_before
    with session_scope() as session:
        document = DocumentRepository(session).get(document_id)
        assert document is not None
        assert document.chunk_count == vectors_before


def test_tag_filter_scopes_the_search(stack) -> None:
    _, store, embedder, _ = stack
    tag = f"scoped-{uuid.uuid4().hex[:6]}"
    ingest(stack, POLICY, f"scoped_{uuid.uuid4().hex[:6]}.md", [tag])

    with session_scope() as session:
        retrieval = RetrievalService(session, embedder=embedder, vector_store=store)
        matching = retrieval.search(
            "approval for an expensive trip", scope=SearchFilter(tags=[tag])
        )
        missing = retrieval.search(
            "approval for an expensive trip", scope=SearchFilter(tags=["no-such-tag-exists"])
        )

    assert matching.results
    assert missing.results == []


def test_delete_removes_rows_and_vectors(stack) -> None:
    service, store, _, created = stack
    document_id, _, _ = ingest(stack, POLICY, f"delete_{uuid.uuid4().hex[:6]}.md", ["travel"])
    assert store.count(document_id) > 0

    with session_scope() as session:
        document = DocumentRepository(session).get(document_id)
        assert document is not None
        service.delete_document(session, document)
    created.remove(document_id)

    assert store.count(document_id) == 0
    with session_scope() as session:
        assert DocumentRepository(session).get(document_id) is None


def test_content_duplicate_is_folded_into_the_original(stack) -> None:
    """The same document re-exported (different bytes, same text) must not be re-indexed."""
    _, store, _, _ = stack
    original_id, _, _ = ingest(stack, POLICY, f"orig_{uuid.uuid4().hex[:6]}.md", ["travel"])
    vectors_before = store.count()

    twin_id, duplicate, _ = ingest(
        stack, POLICY + b"\n\n   \n", f"reexport_{uuid.uuid4().hex[:6]}.md", ["travel"]
    )

    assert duplicate is False, "different bytes must pass the file-hash check"
    assert store.count() == vectors_before, "no new vectors for identical content"
    with session_scope() as session:
        twin = DocumentRepository(session).get(twin_id)
        assert twin is not None
        assert twin.status == DocumentStatus.DUPLICATE
        assert twin.duplicate_of_id == original_id
        assert twin.chunk_count == 0


def test_keyword_search_survives_a_full_natural_language_question(stack) -> None:
    """Regression: an AND-based tsquery matched nothing for question-shaped input."""
    ingest(stack, POLICY, f"keyword_{uuid.uuid4().hex[:6]}.md", ["travel"])
    with session_scope() as session:
        hits = DocumentRepository(session).keyword_search(
            "how far in advance do I have to book my flights for a work trip?", limit=5
        )
    assert hits


def test_unsearchable_documents_are_excluded_from_the_tag_vocabulary(stack) -> None:
    _, _, _, _ = stack
    with session_scope() as session:
        ready_only = {
            item.name for item in TagRepository(session).list_with_counts(ready_only=True)
        }
        everything = {
            item.name for item in TagRepository(session).list_with_counts(ready_only=False)
        }
    assert ready_only <= everything
