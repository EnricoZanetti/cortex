"""Chunking behaviour.

These tests pin the properties the retrieval quality depends on: chunks respect section
boundaries, carry an accurate heading path, stay inside the token budget, and never
collapse a document into a single blob.
"""

from __future__ import annotations

import pytest

from kb.ingestion.chunking import Chunker, count_tokens
from kb.ingestion.parsers import parse_document

POLICY = """\
# Expenses Policy

Owner: Finance. Applies to all staff.

## 1. Submitting a claim

Submit expenses within 60 days of the spend. Itemised receipts are required for anything
above EUR 25.

## 2. Travel

### 2.1 Flights

Economy for flights under six hours. Premium economy above six hours.

### 2.2 Rail

Rail is preferred over air for journeys under four hours.

## 3. Approval

Anything above EUR 1,500 needs department head approval before booking.
"""


def parse(text: str, name: str = "doc.md"):
    return parse_document(text.encode("utf-8"), name, "text/markdown")


def test_heading_path_is_hierarchical(chunker: Chunker) -> None:
    chunks = chunker.chunk(parse(POLICY))
    paths = [chunk.heading_path for chunk in chunks]
    assert all(path is not None and path.startswith("Expenses Policy") for path in paths)
    # The nested subsections must appear somewhere in the hierarchy.
    assert any("2. Travel" in (path or "") for path in paths)


def test_chunks_respect_top_level_section_boundaries(chunker: Chunker) -> None:
    """A chunk must never merge two unrelated top-level sections."""
    chunks = chunker.chunk(parse(POLICY))
    assert len(chunks) > 1, "the whole document collapsed into one chunk"
    for chunk in chunks:
        sections = {"1. Submitting", "2. Travel", "3. Approval"}
        present = {name for name in sections if name in (chunk.heading_path or "")}
        assert len(present) <= 1


def test_chunks_stay_within_the_token_budget(chunker: Chunker) -> None:
    for chunk in chunker.chunk(parse(POLICY)):
        assert chunk.token_count <= chunker.target_tokens * 1.3


def test_embedding_text_prepends_the_heading_path(chunker: Chunker) -> None:
    chunk = chunker.chunk(parse(POLICY))[0]
    assert chunk.heading_path is not None
    assert chunk.embedding_text.startswith(chunk.heading_path)
    assert chunk.text in chunk.embedding_text


def test_long_section_is_split_with_overlap() -> None:
    """An over-long section is split, and consecutive pieces share text."""
    sentences = " ".join(
        f"Rule number {index} states that the reporting threshold is {index * 100} euro."
        for index in range(200)
    )
    chunker = Chunker(target_tokens=120, overlap_tokens=40, min_tokens=10)
    chunks = chunker.chunk(parse(f"# Long\n\n## Section\n\n{sentences}\n"))

    assert len(chunks) > 2
    for chunk in chunks:
        assert chunk.token_count <= 120 * 1.3
    # Overlap: the tail of one chunk reappears at the head of the next.
    first_tail = chunks[0].text.split()[-6:]
    assert " ".join(first_tail) in chunks[1].text


def test_indexes_are_contiguous_and_ordered(chunker: Chunker) -> None:
    chunks = chunker.chunk(parse(POLICY))
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_numbered_list_items_are_not_treated_as_headings(chunker: Chunker) -> None:
    """Regression: '3. Install the agent' in a markdown list is a list item, not a heading."""
    document = parse(
        "# Setup Guide\n\n## First login\n\n"
        "1. Set your password.\n2. Enrol MFA.\n3. Install the device management agent.\n"
    )
    chunks = chunker.chunk(document)
    assert all("Install the device management agent" not in (c.heading_path or "") for c in chunks)


def test_plain_text_numbered_sections_are_detected(chunker: Chunker) -> None:
    """A .txt export has no markdown markers, so numbered sections must be recovered."""
    text = (
        "HR HANDBOOK\n\n"
        "1. PAY\n\nSalary is paid on the 27th of each month without exception.\n\n"
        "2. LEAVE\n\nYou get 25 days of annual leave per year plus public holidays.\n"
    )
    chunks = chunker.chunk(parse(text, "handbook.txt"))
    paths = " ".join(chunk.heading_path or "" for chunk in chunks)
    assert "1. PAY" in paths
    assert "2. LEAVE" in paths


def test_bare_number_at_line_start_is_not_a_heading(chunker: Chunker) -> None:
    """Regression: '25 days per year ...' must not be parsed as heading number 25."""
    text = "HANDBOOK\n\n2.2 Carry over\n\n25 days per year plus public holidays are granted.\n"
    chunks = chunker.chunk(parse(text, "handbook.txt"))
    assert all("25 days per year" not in (chunk.heading_path or "") for chunk in chunks)


@pytest.mark.parametrize("text", ["", "   \n\n  \n"])
def test_empty_document_is_rejected(text: str) -> None:
    from kb.ingestion.parsers import DocumentParseError

    with pytest.raises(DocumentParseError):
        parse(text)


def test_count_tokens_uses_a_real_tokenizer() -> None:
    assert count_tokens("hello world") < count_tokens("hello world " * 50)
