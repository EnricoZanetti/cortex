"""Shared test fixtures.

The unit suite is hermetic: no database, no vector store, no network. Tests that need
Postgres and Qdrant are marked ``integration`` and skip automatically when those services
are not reachable, so ``pytest`` is always runnable on a clean checkout.
"""

from __future__ import annotations

import os

import pytest

# Settings are read at import time, so the environment must be primed before any `kb`
# module is imported.
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("MCP_API_KEY", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("STORAGE_DIR", "/tmp/kb-test-uploads")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-prod")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires a running Postgres and Qdrant")


@pytest.fixture
def chunker():
    from kb.ingestion.chunking import Chunker

    return Chunker(target_tokens=200, overlap_tokens=30, min_tokens=40)


@pytest.fixture
def embedder():
    from kb.ingestion.embeddings import HashEmbedder

    return HashEmbedder(dimensions=64)
