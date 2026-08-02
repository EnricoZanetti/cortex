"""Output schemas for the MCP tools.

These models *are* the contract the LLM reads. Every field carries a description because
the JSON Schema generated from them is sent to the model alongside the tool description,
and a field the model does not understand is a field it will ignore or misuse.

Two deliberate choices:

* **All three search tools return the same ``SearchToolResponse``.** The agent learns one
  result shape, so switching from ``search`` to ``search_by_tag`` costs it nothing.
* **Every response carries a ``guidance`` string.** An empty result list is the moment an
  agent is most likely to give up or hallucinate; telling it what to try next
  ("no matches under tag 'hr' -- 4 tags exist, or retry with `search` across all
  documents") converts a dead end into a recoverable step.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    """One passage retrieved from the knowledge base, with everything needed to cite it."""

    chunk_id: str = Field(
        description=(
            "Stable id of this passage. Pass it to `fetch_chunk_context` to read the "
            "surrounding text when the passage looks cut off."
        )
    )
    document_id: str = Field(
        description="Id of the source document. Use it with `search_by_document`."
    )
    filename: str = Field(description="Source filename, suitable for citing to the user.")
    document_title: str | None = Field(
        default=None, description="Document title, if one could be extracted."
    )
    tags: list[str] = Field(default_factory=list, description="Tags on the source document.")
    heading_path: str | None = Field(
        default=None,
        description=(
            "Section this passage came from, e.g. 'AML Policy > 4. Reporting > 4.2 "
            "Thresholds'. Cite this alongside the filename."
        ),
    )
    page: int | None = Field(default=None, description="Page number, for PDFs.")
    chunk_index: int = Field(
        description="Position of this passage within the document (0-based, reading order)."
    )
    text: str = Field(description="The passage text. Ground your answer in this, verbatim.")
    truncated: bool = Field(
        description=(
            "True if the passage was shortened to fit the character budget. If the answer "
            "seems incomplete, call `fetch_chunk_context` with this chunk_id."
        )
    )
    relevance: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How this passage ranks against the OTHER passages in this result set "
            "(1.0 = best match here). Use it to compare results with each other. It is "
            "not a confidence score -- the top hit always scores 1.0, even when nothing "
            "relevant exists. For that judgement use `similarity`."
        ),
    )
    similarity: float | None = Field(
        default=None,
        description=(
            "Absolute semantic similarity to the query, 0..1, independent of the other "
            "results. Below roughly 0.3 the passage is probably off-topic no matter how "
            "high its `relevance` is. Null when the server runs without a semantic "
            "embedding model."
        ),
    )
    matched_by: list[str] = Field(
        default_factory=list,
        description=(
            "Which retrievers found this passage: 'semantic' (meaning), 'keyword' (exact "
            "terms), or both. Hits matched by both are usually the strongest."
        ),
    )


class SearchToolResponse(BaseModel):
    """Shared response of `search`, `search_by_tag` and `search_by_document`."""

    query: str = Field(description="The query that was executed.")
    scope: str = Field(
        description=(
            "Human-readable description of what was searched, e.g. 'entire knowledge base' "
            "or \"documents tagged 'compliance'\". Useful when citing your source coverage."
        )
    )
    results: list[SearchHit] = Field(
        default_factory=list, description="Passages ordered by relevance, best first."
    )
    result_count: int = Field(description="Number of passages returned.")
    documents_searched: int = Field(description="How many documents were in scope for this search.")
    guidance: str | None = Field(
        default=None,
        description=(
            "What to do next, especially when results are empty or weak. Follow it before "
            "telling the user the knowledge base has no answer."
        ),
    )


class DocumentInfo(BaseModel):
    """Metadata for one document in the knowledge base."""

    document_id: str = Field(
        description="Pass this to `search_by_document` or `get_document_summary`."
    )
    filename: str
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Literal["pending", "processing", "ready", "failed"] = Field(
        description=(
            "Ingestion status. Only 'ready' documents are searchable; 'processing' means "
            "the document was uploaded recently and will become searchable shortly."
        )
    )
    chunk_count: int = Field(description="Number of indexed passages. 0 means not yet searchable.")
    page_count: int | None = Field(default=None, description="Pages, for PDFs.")
    uploaded_at: dt.datetime = Field(description="When the document entered the knowledge base.")


class DocumentListResponse(BaseModel):
    """Response of `list_documents`."""

    documents: list[DocumentInfo] = Field(default_factory=list)
    returned: int = Field(description="Documents in this page.")
    total_matching: int = Field(
        description="Total documents matching the filter, across all pages."
    )
    offset: int = Field(description="Offset of this page; add `returned` to it to page forward.")
    guidance: str | None = Field(
        default=None, description="What to do next, e.g. how to page or narrow the list."
    )


class TagInfo(BaseModel):
    """One tag in the vocabulary."""

    tag: str = Field(description="Exact tag string. Pass it verbatim to `search_by_tag`.")
    document_count: int = Field(description="Number of searchable documents carrying this tag.")


class TagListResponse(BaseModel):
    """Response of `list_tags`."""

    tags: list[TagInfo] = Field(
        default_factory=list, description="Tags ordered by document count, most used first."
    )
    total_tags: int
    guidance: str | None = None


class OutlineEntry(BaseModel):
    """One section of a document's outline."""

    heading_path: str | None = Field(default=None, description="Section heading path.")
    first_chunk_index: int = Field(description="Index of this section's first passage.")
    last_chunk_index: int = Field(description="Index of this section's last passage.")
    page: int | None = None


class DocumentSummaryResponse(BaseModel):
    """Response of `get_document_summary`."""

    document_id: str
    filename: str
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Literal["pending", "processing", "ready", "failed", "duplicate"]
    summary: str = Field(
        description=(
            "Extractive summary: the document's opening prose. Faithful to the source, but "
            "it describes only the beginning -- never answer a content question from this "
            "alone, use `search_by_document` instead."
        )
    )
    outline: list[OutlineEntry] = Field(
        default_factory=list,
        description="Section headings in reading order, with the passage range each covers.",
    )
    chunk_count: int
    page_count: int | None = None
    uploaded_at: dt.datetime
    guidance: str | None = None


class ChunkContextResponse(BaseModel):
    """Response of `fetch_chunk_context`."""

    document_id: str
    filename: str
    heading_path: str | None = None
    requested_chunk_index: int = Field(description="Index of the chunk you asked about.")
    first_chunk_index: int = Field(description="Index of the first passage returned.")
    last_chunk_index: int = Field(description="Index of the last passage returned.")
    text: str = Field(
        description=(
            "The requested passage joined with its neighbours, in reading order and "
            "untruncated. Use this as the grounding text."
        )
    )
    chunk_count: int = Field(description="Total passages in the document.")
    guidance: str | None = None
