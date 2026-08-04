"""The MCP server: the knowledge base as an agent-ready toolset.

Transport is **Streamable HTTP** at ``/mcp``, in stateless mode: every request carries
everything needed to serve it, so the server can run behind an ordinary load balancer and
scale horizontally without sticky sessions. Authentication is a bearer token enforced by
:class:`kb.mcp_server.auth.BearerAuthMiddleware` in front of the transport.

The tool docstrings below are the product. They are written for an LLM reader, not a
human maintainer: each says what the tool returns, when to reach for it, and -- crucially
-- which sibling tool to use instead when it is the wrong choice.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Annotated, Literal, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from kb.config import Settings, get_settings
from kb.ingestion.embeddings import build_embedder
from kb.logging import configure_logging, get_logger
from kb.mcp_server.auth import BearerAuthMiddleware
from kb.mcp_server.schemas import (
    ChunkContextResponse,
    DocumentListResponse,
    DocumentSummaryResponse,
    SearchToolResponse,
    TagListResponse,
)
from kb.mcp_server.tools import (
    MaxChars,
    MinRelevance,
    Query,
    ToolContext,
    ToolInputError,
    TopK,
    fetch_chunk_context_impl,
    get_document_summary_impl,
    list_documents_impl,
    list_tags_impl,
    search_by_document_impl,
    search_by_tag_impl,
    search_impl,
)
from kb.retrieval.vector_store import VectorStore

logger = get_logger(__name__)

_F = TypeVar("_F", bound=Callable[..., object])

SERVER_INSTRUCTIONS = """\
This server exposes a company knowledge base of internal documents (compliance policies,
product manuals, onboarding guides, HR and FAQ material). Every document is tagged by
topic and indexed passage by passage.

Recommended workflow for answering a question:
1. Call `search` first. It covers every document and is the right default.
2. If the results are noisy and the question clearly belongs to one topic area, call
   `list_tags` and then `search_by_tag`.
3. For a follow-up about a specific document, call `search_by_document`.
4. Use `list_documents` to see what exists, and `get_document_summary` to learn what a
   single document covers before drilling in.
5. If a returned passage is marked `truncated`, call `fetch_chunk_context` before quoting.

Always ground answers in the returned passages and cite the filename and heading_path.
If the tools return nothing relevant, say so rather than answering from general knowledge.
"""


def build_server() -> MCPServer:
    """Construct the MCP server and register every tool."""
    context = ToolContext(embedder=build_embedder(), vector_store=VectorStore())

    server = MCPServer(
        name="cortex",
        title="Cortex: Company Document Knowledge Base",
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
    )

    read_only = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

    def tool(**kwargs: object) -> Callable[[_F], _F]:
        """Register a tool, normalising its docstring first.

        Docstrings arrive with the source indentation still attached. That indentation is
        shipped verbatim to the model inside the tool description, so it is stripped here
        rather than being written unindented in the source (which would be unreadable).
        """

        def decorator(fn: _F) -> _F:
            fn.__doc__ = inspect.cleandoc(fn.__doc__ or "")
            server.tool(**kwargs)(fn)  # type: ignore[arg-type]
            return fn

        return decorator

    # --- discovery tools ------------------------------------------------------------

    @tool(title="List documents", annotations=read_only)
    def list_documents(
        tags: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=20,
                description=(
                    "Only list documents carrying these tags. Get valid values from "
                    "`list_tags` -- an unknown tag returns nothing. Omit to list everything."
                ),
            ),
        ] = None,
        tag_match: Annotated[
            Literal["any", "all"],
            Field(
                default="any",
                description=(
                    "'any' = documents with at least one of the tags (default). "
                    "'all' = only documents carrying every listed tag."
                ),
            ),
        ] = "any",
        filename_contains: Annotated[
            str | None,
            Field(
                default=None,
                max_length=200,
                description=(
                    "Case-insensitive substring filter on filename or title, e.g. 'onboarding'. "
                    "Use it to find a document you already know the name of."
                ),
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(default=20, ge=1, le=100, description="Maximum documents to return."),
        ] = 20,
        offset: Annotated[
            int,
            Field(default=0, ge=0, description="Skip this many documents; use it to page."),
        ] = 0,
        sort: Annotated[
            Literal["recent", "name"],
            Field(default="recent", description="'recent' = newest uploads first; 'name' = A-Z."),
        ] = "recent",
    ) -> DocumentListResponse:
        """Browse the knowledge base inventory: which documents exist, their tags and status.

        Use this to answer "what documents do we have (about X)?", to check whether a
        specific document has been uploaded, or to obtain a `document_id` before calling
        `search_by_document` or `get_document_summary`.

        Do NOT use this to answer questions about what the documents *say* -- it returns
        metadata only, never document content. For content, use `search`.
        """
        return list_documents_impl(
            context,
            tags=tags,
            tag_match=tag_match,
            filename_contains=filename_contains,
            limit=limit,
            offset=offset,
            sort=sort,
        )

    @tool(title="List tags", annotations=read_only)
    def list_tags() -> TagListResponse:
        """List every topic tag in use, with the number of documents carrying each.

        Tags are a closed, curated vocabulary (e.g. 'compliance', 'onboarding', 'product',
        'hr'). Call this BEFORE `search_by_tag` whenever you are not certain a tag exists:
        `search_by_tag` with an invented tag matches nothing and wastes a turn.

        This is also a fast way to describe the knowledge base's coverage to a user.
        """
        return list_tags_impl(context)

    # --- search tools ----------------------------------------------------------------

    @tool(title="Search the knowledge base", annotations=read_only)
    def search(
        query: Query,
        top_k: TopK = 6,
        min_relevance: MinRelevance = 0.0,
        max_chars_per_result: MaxChars = 800,
    ) -> SearchToolResponse:
        """Search every document in the knowledge base and return the most relevant passages.

        THIS IS THE DEFAULT TOOL for any question about company policy, process or product
        information. Start here. Retrieval is hybrid -- semantic meaning plus exact keyword
        matching -- so it handles both "how do I escalate a suspicious transaction?" and
        "Form ADV filing deadline".

        Use `search_by_tag` instead only when a corpus-wide search returns off-topic
        results and you have confirmed a relevant tag via `list_tags`. Use
        `search_by_document` instead when the user is asking about one specific document
        you have already identified.

        Each result includes the source filename and section heading: cite them.
        """
        return search_impl(
            context,
            query=query,
            top_k=top_k,
            min_relevance=min_relevance,
            max_chars_per_result=max_chars_per_result,
        )

    @tool(title="Search within a topic", annotations=read_only)
    def search_by_tag(
        query: Query,
        tags: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=20,
                description=(
                    "Tags to restrict the search to. Must be exact values from `list_tags`; "
                    "unknown tags are ignored and reported back in `guidance`."
                ),
            ),
        ],
        tag_match: Annotated[
            Literal["any", "all"],
            Field(
                default="any",
                description=(
                    "'any' (default) searches documents carrying at least one of the tags -- "
                    "usually what you want. 'all' narrows to documents carrying every tag, "
                    "which can easily match zero documents."
                ),
            ),
        ] = "any",
        top_k: TopK = 6,
        min_relevance: MinRelevance = 0.0,
        max_chars_per_result: MaxChars = 800,
    ) -> SearchToolResponse:
        """Search only the documents carrying the given topic tags.

        Use this when the question clearly belongs to a known topic area and a corpus-wide
        `search` returned material from unrelated areas -- for example restricting an
        onboarding question to tag 'onboarding' so product manuals cannot crowd out the
        answer. Confirm the tags exist with `list_tags` first.

        Do NOT use this as your first move on a general question: narrowing too early is
        the most common way to miss an answer that is filed under a different tag. If this
        returns nothing, fall back to `search`.

        Returns the same result shape as `search`.
        """
        return search_by_tag_impl(
            context,
            query=query,
            tags=tags,
            tag_match=tag_match,
            top_k=top_k,
            min_relevance=min_relevance,
            max_chars_per_result=max_chars_per_result,
        )

    @tool(title="Search within specific documents", annotations=read_only)
    def search_by_document(
        query: Query,
        documents: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=10,
                description=(
                    "Documents to search, given as `document_id` values (preferred, exact) "
                    "or filenames/titles, which are matched case-insensitively. You may mix "
                    "both. Get exact values from `list_documents` or from an earlier search "
                    "result. If a name is ambiguous the tool replies with the candidates."
                ),
            ),
        ],
        top_k: Annotated[
            int,
            Field(
                default=8,
                ge=1,
                le=20,
                description=(
                    "How many passages to return. Defaults higher than `search` because a "
                    "single-document search often needs several sections of the same text."
                ),
            ),
        ] = 8,
        min_relevance: MinRelevance = 0.0,
        max_chars_per_result: MaxChars = 800,
    ) -> SearchToolResponse:
        """Search inside one or more specific documents, identified by id or by name.

        Use this for follow-up questions about a document already in play ("and what does
        the AML policy say about thresholds?"), and when you need to extract everything a
        particular document says about a topic.

        Do NOT use this to answer a general question: it cannot see any document you did
        not name, so a miss here does not mean the knowledge base lacks the answer -- use
        `search` for that. To learn what a document covers before searching it, call
        `get_document_summary`.

        Results are restricted to the named documents but otherwise identical in shape to
        `search`; each carries `chunk_index` so you can read passages in document order.
        """
        return search_by_document_impl(
            context,
            query=query,
            documents=documents,
            top_k=top_k,
            min_relevance=min_relevance,
            max_chars_per_result=max_chars_per_result,
        )

    # --- context tools -----------------------------------------------------------------

    @tool(title="Summarise a document", annotations=read_only)
    def get_document_summary(
        document: Annotated[
            str,
            Field(
                min_length=1,
                max_length=300,
                description=(
                    "A `document_id` (preferred) or a filename/title. Get one from "
                    "`list_documents` or from any search result."
                ),
            ),
        ],
        include_outline: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "Include the section-heading outline. Keep it on to decide which part "
                    "of the document to query next; turn it off for a shorter response."
                ),
            ),
        ] = True,
    ) -> DocumentSummaryResponse:
        """Get an overview of one document: title, tags, summary and section outline.

        Use this to decide whether a document is worth searching, and to answer "what is
        in this document?" without spending a retrieval call. The outline shows which
        sections exist, which is the fastest way to orient yourself in a long policy.

        Do NOT use this to answer a specific factual question: the summary covers only the
        opening of the document. For facts, call `search_by_document` with the same
        document.
        """
        try:
            return get_document_summary_impl(
                context, document=document, include_outline=include_outline
            )
        except ToolInputError as exc:
            raise ToolError(str(exc)) from exc

    @tool(title="Expand a passage", annotations=read_only)
    def fetch_chunk_context(
        chunk_id: Annotated[
            str,
            Field(description=("The `chunk_id` of a passage returned by one of the search tools.")),
        ],
        before: Annotated[
            int,
            Field(
                default=1,
                ge=0,
                le=5,
                description="How many preceding passages to include (0-5).",
            ),
        ] = 1,
        after: Annotated[
            int,
            Field(
                default=1, ge=0, le=5, description="How many following passages to include (0-5)."
            ),
        ] = 1,
    ) -> ChunkContextResponse:
        """Read a retrieved passage in full, together with its neighbouring passages.

        Use this when a search result is marked `truncated: true`, when a passage starts or
        ends mid-thought, or when you need the sentence that follows a quoted rule (for
        example the exception clause that comes after a requirement). This is the correct
        way to get more text -- it is far cheaper than re-running a search with a larger
        character budget.

        Do NOT use this to find new information on a different topic: it only expands
        around a passage you already retrieved. Use a search tool for that.
        """
        try:
            return fetch_chunk_context_impl(context, chunk_id=chunk_id, before=before, after=after)
        except ToolInputError as exc:
            raise ToolError(str(exc)) from exc

    return server


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    """Configure DNS-rebinding protection for the MCP transport.

    The middleware matches Host/Origin exactly (or as ``host:*``); it has no notion of a
    global wildcard. So ``MCP_ALLOWED_HOSTS="*"`` -- the default, and the only workable
    setting when the service sits behind a load balancer or tunnel whose hostname we do
    not know -- is expressed as *protection disabled*. Setting an explicit host list
    turns the check on, which is what a fixed-domain deployment should do.
    """
    wildcard = "*" in settings.mcp_allowed_hosts
    if wildcard:
        logger.info("dns_rebinding_protection_disabled", reason="MCP_ALLOWED_HOSTS='*'")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=not wildcard,
        allowed_hosts=[host for host in settings.mcp_allowed_hosts if host != "*"],
        allowed_origins=[origin for origin in settings.mcp_allowed_origins if origin != "*"],
    )


async def _health(request: Request) -> JSONResponse:
    """Unauthenticated liveness probe for the container orchestrator."""
    return JSONResponse({"status": "ok", "transport": "streamable-http"})


def create_app() -> Starlette:
    """Build the ASGI app: auth middleware wrapping the Streamable HTTP MCP transport."""
    configure_logging()
    settings = get_settings()
    server = build_server()

    mcp_app = server.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        stateless_http=True,
        transport_security=_transport_security(settings),
    )
    # Unauthenticated liveness probe, added before the auth middleware wraps the app.
    mcp_app.router.routes.append(Route("/healthz", _health, methods=["GET"]))
    mcp_app.add_middleware(
        BearerAuthMiddleware,
        api_key=settings.mcp_api_key.get_secret_value(),
        protected_prefix=settings.mcp_path,
    )
    logger.info("mcp_server_ready", path=settings.mcp_path, transport="streamable-http")
    return mcp_app


app = create_app()
