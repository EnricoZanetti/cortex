"""The event vocabulary a chat turn emits.

Every provider adapter yields this same small set of events, so the API layer and
the browser stay provider-neutral: switching from Claude to Gemini changes nothing
downstream of here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A fragment of the assistant's answer, streamed as it is produced."""

    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """The assistant decided to use a knowledge base tool.

    Surfaced in the UI so a user can see *which* tool ran and with what arguments.
    That visibility is the point of the demo: it shows the agent choosing between
    `search`, `search_by_tag` and `search_by_document` rather than guessing.
    """

    name: str
    arguments: dict[str, Any]
    type: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A tool returned. Carries the citations the UI renders under the answer."""

    name: str
    is_error: bool
    result_count: int | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    guidance: str | None = None
    type: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True, slots=True)
class TurnError:
    """The turn could not be completed."""

    message: str
    type: Literal["error"] = "error"


@dataclass(frozen=True, slots=True)
class TurnDone:
    """The turn finished normally."""

    stop_reason: str | None = None
    type: Literal["done"] = "done"


ChatEvent = TextDelta | ToolCall | ToolResult | TurnError | TurnDone


def citations_from(
    structured: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], int | None, str | None]:
    """Pull the citation fields out of a search tool's structured output.

    Returns ``(citations, result_count, guidance)``. Tools that return something
    other than search hits (``list_tags``, for instance) simply yield no citations.
    """
    if not structured:
        return [], None, None
    results = structured.get("results")
    guidance = structured.get("guidance")
    if not isinstance(results, list):
        return [], None, guidance if isinstance(guidance, str) else None
    citations = [
        {
            "filename": hit.get("filename"),
            "heading_path": hit.get("heading_path"),
            "page": hit.get("page"),
            "document_id": hit.get("document_id"),
            "chunk_id": hit.get("chunk_id"),
            "relevance": hit.get("relevance"),
        }
        for hit in results
        if isinstance(hit, dict)
    ]
    return citations, len(citations), guidance if isinstance(guidance, str) else None
