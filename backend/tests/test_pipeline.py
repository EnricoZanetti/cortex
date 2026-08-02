"""Ingestion pipeline units that need no database: fingerprints, ids, parsing."""

from __future__ import annotations

import uuid

import pytest

from kb.ingestion.parsers import (
    UnsupportedDocumentError,
    parse_document,
    resolve_parser_kind,
)
from kb.ingestion.pipeline import chunk_id_for, content_fingerprint, file_fingerprint


class TestFileFingerprint:
    def test_identical_bytes_share_a_fingerprint(self) -> None:
        assert file_fingerprint(b"hello") == file_fingerprint(b"hello")

    def test_one_changed_byte_changes_the_fingerprint(self) -> None:
        assert file_fingerprint(b"hello") != file_fingerprint(b"hellp")


class TestContentFingerprint:
    """Level 2 dedup: the same content re-saved must fingerprint identically."""

    @pytest.mark.parametrize(
        "variant",
        [
            "The policy applies to all staff.",
            "The  policy   applies to all staff.",
            "The policy applies\nto all staff.",
            "  The policy applies to all staff.  ",
            "THE POLICY APPLIES TO ALL STAFF.",
        ],
    )
    def test_whitespace_and_case_variants_collide(self, variant: str) -> None:
        canonical = content_fingerprint("The policy applies to all staff.")
        assert content_fingerprint(variant) == canonical

    def test_different_content_does_not_collide(self) -> None:
        assert content_fingerprint("threshold is 10000") != content_fingerprint(
            "threshold is 15000"
        )


class TestChunkIds:
    """Level 3 dedup: deterministic ids make re-ingestion an upsert."""

    def test_same_document_and_content_yield_the_same_id(self) -> None:
        document_id = uuid.uuid4()
        assert chunk_id_for(document_id, "abc") == chunk_id_for(document_id, "abc")

    def test_different_documents_yield_different_ids(self) -> None:
        assert chunk_id_for(uuid.uuid4(), "abc") != chunk_id_for(uuid.uuid4(), "abc")

    def test_different_content_yields_different_ids(self) -> None:
        document_id = uuid.uuid4()
        assert chunk_id_for(document_id, "abc") != chunk_id_for(document_id, "abd")


class TestParserSelection:
    @pytest.mark.parametrize(
        ("filename", "content_type", "expected"),
        [
            ("policy.pdf", "application/pdf", "pdf"),
            ("policy.PDF", None, "pdf"),
            ("faq.txt", "text/plain", "text"),
            ("readme.md", "text/markdown", "text"),
            # Browsers often send octet-stream; the extension must win.
            ("notes.md", "application/octet-stream", "text"),
            ("scan.pdf", "application/octet-stream", "pdf"),
        ],
    )
    def test_resolves_supported_types(
        self, filename: str, content_type: str | None, expected: str
    ) -> None:
        assert resolve_parser_kind(filename, content_type) == expected

    @pytest.mark.parametrize("filename", ["script.sh", "archive.zip", "image.png", "noext"])
    def test_rejects_unsupported_types(self, filename: str) -> None:
        with pytest.raises(UnsupportedDocumentError) as excinfo:
            resolve_parser_kind(filename, "application/octet-stream")
        # The error must tell the user what *is* supported.
        assert ".pdf" in str(excinfo.value)


def test_parse_document_falls_back_to_the_filename_for_a_title() -> None:
    parsed = parse_document(b"Just a body with no heading at all.", "my_policy-v2.txt", None)
    assert parsed.title == "my policy v2"
