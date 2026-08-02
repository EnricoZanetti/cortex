"""MCP tool implementations.

Each tool is a thin, well-documented adapter over the shared core services. The value
added here is entirely in the *interface*: the name, the description, the input schema and
the shape of the response are what an LLM uses to decide what to call and what to do with
the answer.

Interface conventions applied to every tool
-------------------------------------------
* The description opens with what the tool does in one line, then **when to use it** and
  **when not to (and which sibling tool to use instead)**. Redirection is the single most
  effective way to stop an agent reaching for the wrong tool.
* Every parameter has a description, a type, a default and -- where the value space is
  bounded -- a constraint, so the model cannot invent an out-of-range value.
* Responses always contain the identifiers needed for the natural next call.
* Responses always contain ``guidance``: an explicit next step, especially when the result
  set is empty.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field
from sqlalchemy.orm import Session

from kb.db.models import DocumentStatus
from kb.db.repositories import DocumentRepository, TagRepository, normalize_tag
from kb.db.session import session_scope
from kb.ingestion.embeddings import Embedder
from kb.mcp_server.schemas import (
    ChunkContextResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentSummaryResponse,
    OutlineEntry,
    SearchHit,
    SearchToolResponse,
    TagInfo,
    TagListResponse,
)
from kb.retrieval.resolver import DocumentResolver, UnresolvedReference
from kb.retrieval.service import RetrievalService, SearchFilter, SearchResponse, SearchResult
from kb.retrieval.vector_store import VectorStore

# --- reusable parameter annotations ------------------------------------------------
# Defined once so the three search tools present a genuinely identical interface for the
# parameters they share -- an agent that has learned `top_k` on `search` already knows it
# on `search_by_tag`.

Query = Annotated[
    str,
    Field(
        min_length=2,
        max_length=1000,
        description=(
            "Natural-language question or topic to search for. Write it as the user would "
            "ask it -- full questions work better than keywords, because retrieval is "
            "semantic. Include distinctive terms (regulation names, form numbers, "
            "thresholds) when you know them: they are matched exactly as well as semantically."
        ),
    ),
]

TopK = Annotated[
    int,
    Field(
        default=6,
        ge=1,
        le=20,
        description=(
            "How many passages to return. 6 is a good default for answering one question; "
            "raise it to 10-15 only when compiling a list or comparing across documents, "
            "since every extra passage costs context."
        ),
    ),
]

MinRelevance = Annotated[
    float,
    Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Drop passages scoring below this fraction of the best hit (0 = keep all). "
            "Leave at 0 unless you are checking whether the knowledge base covers a topic "
            "at all, where 0.5 filters out weak matches."
        ),
    ),
]

MaxChars = Annotated[
    int,
    Field(
        default=800,
        ge=200,
        le=4000,
        description=(
            "Character budget per passage. Passages longer than this are truncated and "
            "flagged with `truncated: true`; use `fetch_chunk_context` to read the rest "
            "rather than raising this for every search."
        ),
    ),
]


class ToolContext:
    """Everything a tool needs, built once at server start."""

    def __init__(self, *, embedder: Embedder, vector_store: VectorStore) -> None:
        self.embedder = embedder
        self.vector_store = vector_store

    def retrieval(self, session: Session) -> RetrievalService:
        return RetrievalService(session, embedder=self.embedder, vector_store=self.vector_store)


# --- shared helpers -----------------------------------------------------------------


def _to_hit(result: SearchResult) -> SearchHit:
    return SearchHit(
        chunk_id=str(result.chunk_id),
        document_id=str(result.document_id),
        filename=result.filename,
        document_title=result.document_title,
        tags=result.tags,
        heading_path=result.heading_path,
        page=result.page,
        chunk_index=result.chunk_index,
        text=result.text,
        truncated=result.truncated,
        relevance=result.relevance,
        similarity=round(result.similarity, 4) if result.similarity is not None else None,
        matched_by=result.matched_by,
    )


def _build_response(
    *, query: str, scope: str, response: SearchResponse, guidance: str | None
) -> SearchToolResponse:
    return SearchToolResponse(
        query=query,
        scope=scope,
        results=[_to_hit(result) for result in response.results],
        result_count=len(response.results),
        documents_searched=response.documents_searched,
        guidance=guidance,
    )


def _empty_corpus_guidance(session: Session) -> str | None:
    """Distinguish 'no match' from 'nothing indexed yet' -- very different for the agent."""
    ready, _ = DocumentRepository(session).list_documents(statuses=[DocumentStatus.READY], limit=1)
    if ready:
        return None
    pending, _ = DocumentRepository(session).list_documents(
        statuses=[DocumentStatus.PENDING, DocumentStatus.PROCESSING], limit=1
    )
    if pending:
        return (
            "The knowledge base has no searchable documents yet: uploads are still being "
            "processed. Tell the user to retry in a moment."
        )
    return (
        "The knowledge base is empty -- no documents have been uploaded. Tell the user "
        "this rather than answering from your own knowledge."
    )


# --- tool implementations -------------------------------------------------------------


def list_documents_impl(
    context: ToolContext,
    *,
    tags: list[str] | None,
    tag_match: Literal["any", "all"],
    filename_contains: str | None,
    limit: int,
    offset: int,
    sort: Literal["recent", "name"],
) -> DocumentListResponse:
    with session_scope() as session:
        repo = DocumentRepository(session)
        documents, total = repo.list_documents(
            tags=tags or None,
            tag_match=tag_match,
            filename_contains=filename_contains,
            # Content-duplicate rows are an artefact of the upload process. Showing them
            # to an agent would suggest the knowledge base holds two copies of a policy
            # and invite a pointless second search, so they are hidden here. They remain
            # visible in the management UI, where they are the user's business.
            statuses=[
                DocumentStatus.READY,
                DocumentStatus.PENDING,
                DocumentStatus.PROCESSING,
                DocumentStatus.FAILED,
            ],
            limit=limit,
            offset=offset,
            sort=sort,
        )
        infos = [
            DocumentInfo(
                document_id=str(document.id),
                filename=document.filename,
                title=document.title,
                tags=document.tag_names,
                status=document.status.value,  # type: ignore[arg-type]
                chunk_count=document.chunk_count,
                page_count=document.page_count,
                uploaded_at=document.created_at,
            )
            for document in documents
        ]

        if not infos and total == 0:
            if tags or filename_contains:
                available = ", ".join(
                    item.name for item in TagRepository(session).list_with_counts()
                )
                guidance = (
                    "No documents match that filter. "
                    + (f"Available tags: {available}. " if available else "")
                    + "Call list_documents with no filter to see the whole knowledge base."
                )
            else:
                guidance = "The knowledge base is empty; no documents have been uploaded yet."
        elif offset + len(infos) < total:
            guidance = (
                f"Showing {len(infos)} of {total}. Call again with offset="
                f"{offset + len(infos)} for the next page, or use `search` if you are "
                "looking for content rather than browsing."
            )
        else:
            guidance = (
                "To answer a question about what these documents say, use `search` "
                "(or `search_by_document` with a document_id from this list)."
            )

        return DocumentListResponse(
            documents=infos,
            returned=len(infos),
            total_matching=total,
            offset=offset,
            guidance=guidance,
        )


def list_tags_impl(context: ToolContext) -> TagListResponse:
    with session_scope() as session:
        counts = TagRepository(session).list_with_counts(ready_only=True)
        tags = [TagInfo(tag=item.name, document_count=item.document_count) for item in counts]
        if not tags:
            guidance = (
                "No tags exist yet, so `search_by_tag` cannot be used. Use `search` to "
                "query the whole knowledge base."
            )
        else:
            guidance = (
                "Pass these strings verbatim to `search_by_tag`. Any other value will "
                "match nothing."
            )
        return TagListResponse(tags=tags, total_tags=len(tags), guidance=guidance)


def search_impl(
    context: ToolContext,
    *,
    query: str,
    top_k: int,
    min_relevance: float,
    max_chars_per_result: int,
) -> SearchToolResponse:
    with session_scope() as session:
        response = context.retrieval(session).search(
            query,
            scope=SearchFilter(),
            top_k=top_k,
            min_relevance=min_relevance,
            max_chars_per_result=max_chars_per_result,
        )
        if response.results:
            guidance = _quality_guidance(response, scoped=False)
        else:
            guidance = _empty_corpus_guidance(session) or (
                "No passage matched this query anywhere in the knowledge base. Try "
                "rephrasing with different terminology, or call `list_documents` / "
                "`list_tags` to see what the knowledge base actually covers. Do not "
                "answer from your own knowledge without telling the user."
            )
        return _build_response(
            query=query, scope="entire knowledge base", response=response, guidance=guidance
        )


def search_by_tag_impl(
    context: ToolContext,
    *,
    query: str,
    tags: list[str],
    tag_match: Literal["any", "all"],
    top_k: int,
    min_relevance: float,
    max_chars_per_result: int,
) -> SearchToolResponse:
    with session_scope() as session:
        normalized = [normalize_tag(tag) for tag in tags if normalize_tag(tag)]
        known = TagRepository(session).existing_names(normalized)
        unknown = [tag for tag in normalized if tag not in known]

        scope = f"documents tagged {', '.join(repr(tag) for tag in normalized)} ({tag_match})"

        if not known:
            available = ", ".join(item.name for item in TagRepository(session).list_with_counts())
            return SearchToolResponse(
                query=query,
                scope=scope,
                results=[],
                result_count=0,
                documents_searched=0,
                guidance=(
                    f"None of these tags exist: {', '.join(unknown)}. "
                    + (
                        f"The available tags are: {available}. Re-call `search_by_tag` with "
                        "one of them"
                        if available
                        else "No tags exist in this knowledge base"
                    )
                    + ", or use `search` to query every document instead."
                ),
            )

        response = context.retrieval(session).search(
            query,
            scope=SearchFilter(tags=sorted(known), tag_match=tag_match),
            top_k=top_k,
            min_relevance=min_relevance,
            max_chars_per_result=max_chars_per_result,
        )

        notes: list[str] = []
        if unknown:
            notes.append(
                f"Ignored unknown tag(s): {', '.join(unknown)}. Call `list_tags` for the "
                "valid vocabulary."
            )
        if response.results:
            notes.append(_quality_guidance(response, scoped=True))
        elif response.documents_searched == 0:
            notes.append(
                "No searchable documents carry these tags"
                + (" together (tag_match='all')." if tag_match == "all" else ".")
                + " Retry with `search` across the whole knowledge base."
            )
        else:
            notes.append(
                f"Nothing matched inside the {response.documents_searched} document(s) with "
                "these tags. The topic may be filed under a different tag: retry with "
                "`search` (no tag filter) before concluding the answer is not available."
            )
        return _build_response(
            query=query, scope=scope, response=response, guidance=" ".join(notes)
        )


def search_by_document_impl(
    context: ToolContext,
    *,
    query: str,
    documents: list[str],
    top_k: int,
    min_relevance: float,
    max_chars_per_result: int,
) -> SearchToolResponse:
    with session_scope() as session:
        outcome = DocumentResolver(session).resolve_many(documents)

        if not outcome.resolved:
            problems = "; ".join(
                f"{item.query!r}: {item.reason}"
                + (
                    " Candidates: "
                    + ", ".join(f"{name} (document_id={did})" for did, name in item.candidates)
                    if item.candidates
                    else ""
                )
                for item in outcome.unresolved
            )
            return SearchToolResponse(
                query=query,
                scope="no documents (none of the references resolved)",
                results=[],
                result_count=0,
                documents_searched=0,
                guidance=(
                    f"Could not resolve any document reference. {problems} "
                    "Call `list_documents` to get exact filenames and document_id values."
                ),
            )

        response = context.retrieval(session).search(
            query,
            scope=SearchFilter(document_ids=outcome.document_ids),
            top_k=top_k,
            min_relevance=min_relevance,
            max_chars_per_result=max_chars_per_result,
        )

        names = ", ".join(reference.filename for reference in outcome.resolved)
        scope = f"documents: {names}"

        notes: list[str] = []
        if outcome.unresolved:
            notes.append(
                "Unresolved reference(s): "
                + "; ".join(f"{item.query!r} ({item.reason})" for item in outcome.unresolved)
                + "."
            )
        if response.results:
            notes.append(
                "Results are restricted to the named document(s); if the answer is not "
                "here, it may live elsewhere -- use `search` to check the whole knowledge base."
            )
        else:
            notes.append(
                f"No passage in {names} matched this query. The document may not cover the "
                "topic: call `get_document_summary` to see what it does cover, or `search` "
                "to look across all documents."
            )
        return _build_response(
            query=query, scope=scope, response=response, guidance=" ".join(notes)
        )


def get_document_summary_impl(
    context: ToolContext, *, document: str, include_outline: bool
) -> DocumentSummaryResponse:
    with session_scope() as session:
        outcome = DocumentResolver(session).resolve_one(document)
        if isinstance(outcome, UnresolvedReference):
            hint = (
                " Candidates: "
                + ", ".join(f"{name} (document_id={did})" for did, name in outcome.candidates)
                if outcome.candidates
                else " Call `list_documents` to see available documents."
            )
            raise ToolInputError(f"{outcome.reason}{hint}")

        repo = DocumentRepository(session)
        record = repo.get(outcome.document_id)
        if record is None:  # pragma: no cover - resolver just read this row
            raise ToolInputError("That document no longer exists. Call `list_documents`.")

        outline: list[OutlineEntry] = []
        if include_outline:
            outline = _build_outline(repo.get_document_chunks(record.id))

        if record.status != DocumentStatus.READY:
            guidance = f"This document is '{record.status.value}' and is not searchable yet" + (
                f" (error: {record.error})." if record.error else "."
            )
        else:
            guidance = (
                "This summary covers only the opening of the document. To answer a specific "
                f"question about it, call `search_by_document` with document_id={record.id}."
            )

        return DocumentSummaryResponse(
            document_id=str(record.id),
            filename=record.filename,
            title=record.title,
            tags=record.tag_names,
            status=record.status.value,
            summary=record.summary or "(no summary available)",
            outline=outline,
            chunk_count=record.chunk_count,
            page_count=record.page_count,
            uploaded_at=record.created_at,
            guidance=guidance,
        )


def fetch_chunk_context_impl(
    context: ToolContext, *, chunk_id: str, before: int, after: int
) -> ChunkContextResponse:
    try:
        identifier = uuid.UUID(chunk_id)
    except ValueError as exc:
        raise ToolInputError(
            f"{chunk_id!r} is not a valid chunk_id. Use a chunk_id returned by a search tool."
        ) from exc

    with session_scope() as session:
        repo = DocumentRepository(session)
        chunk = repo.get_chunk(identifier)
        if chunk is None:
            raise ToolInputError(
                "No passage with that chunk_id. It may belong to a document that has since "
                "been deleted; re-run your search to get current chunk_ids."
            )
        window = repo.get_chunk_window(
            chunk.document_id,
            max(chunk.chunk_index - before, 0),
            chunk.chunk_index + after,
        )
        document = chunk.document
        text = "\n\n".join(item.text for item in window)
        return ChunkContextResponse(
            document_id=str(document.id),
            filename=document.filename,
            heading_path=chunk.heading_path,
            requested_chunk_index=chunk.chunk_index,
            first_chunk_index=window[0].chunk_index,
            last_chunk_index=window[-1].chunk_index,
            text=text,
            chunk_count=document.chunk_count,
            guidance=(
                "This is the untruncated source text. If it still looks cut off, call this "
                "tool again with larger `before`/`after` values."
            ),
        )


# --- support ---------------------------------------------------------------------------


class ToolInputError(ValueError):
    """Raised for agent-correctable input problems; surfaced as the tool's error message."""


#: Cosine similarity below which the top hit is unlikely to be genuinely on topic.
#: `relevance` is normalised against the best hit in the set, so it can never express
#: "nothing here is relevant" -- the top result always scores 1.0. Absolute similarity
#: can, so it is what decides whether the agent gets warned.
WEAK_SIMILARITY = 0.30


def _quality_guidance(response: SearchResponse, *, scoped: bool) -> str:
    """Tell the agent how much to trust this result set."""
    grounding = (
        "Ground your answer in these passages and cite filename plus heading_path. If a "
        "passage is marked truncated, call `fetch_chunk_context` before quoting it."
    )
    best = response.best_similarity

    if best is not None and best < WEAK_SIMILARITY:
        return (
            f"WARNING: the closest passage scores only {best:.2f} absolute similarity, "
            "which usually means the knowledge base does not cover this topic. Read the "
            "passages before relying on them"
            + (
                ", and retry with `search` without the filter."
                if scoped
                else ". If they do not answer the question, tell the user the knowledge "
                "base has nothing on it rather than answering from general knowledge."
            )
        )

    matched_by_both = sum(1 for result in response.results if len(result.matched_by) > 1)
    if matched_by_both == 0 and response.semantic:
        return (
            "No passage matched both semantically and by exact keyword, so these are only "
            "moderate matches -- check they answer the question. " + grounding
        )
    return grounding


def _build_outline(chunks: list) -> list[OutlineEntry]:  # type: ignore[type-arg]
    """Collapse consecutive chunks sharing a heading path into outline entries."""
    entries: list[OutlineEntry] = []
    for chunk in chunks:
        if entries and entries[-1].heading_path == chunk.heading_path:
            entries[-1].last_chunk_index = chunk.chunk_index
            continue
        entries.append(
            OutlineEntry(
                heading_path=chunk.heading_path,
                first_chunk_index=chunk.chunk_index,
                last_chunk_index=chunk.chunk_index,
                page=chunk.page,
            )
        )
    return entries
