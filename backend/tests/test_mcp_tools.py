"""The MCP tool interface.

This is the contract an LLM consumes, so it is tested like a public API: the set of tool
names, which parameters are required, the constraints on each, and the presence of the
"when to use / when not to" guidance that makes tool selection work. A refactor that
silently drops a description or loosens a bound should fail here.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from kb.mcp_server.server import build_server

REQUIRED_BY_THE_BRIEF = {
    "list_documents",
    "list_tags",
    "search",
    "search_by_tag",
    "search_by_document",
}
ADDITIONAL = {"get_document_summary", "fetch_chunk_context"}


@pytest.fixture(scope="module")
def tools() -> dict:
    server = build_server()
    listed = asyncio.run(server.list_tools())
    return {tool.name: tool for tool in listed}


def test_every_required_tool_is_exposed(tools: dict) -> None:
    assert set(tools) >= REQUIRED_BY_THE_BRIEF


def test_the_toolset_is_exactly_what_we_intend(tools: dict) -> None:
    """Guards against accidentally shipping a half-finished tool."""
    assert set(tools) == REQUIRED_BY_THE_BRIEF | ADDITIONAL


@pytest.mark.parametrize(
    ("tool_name", "required"),
    [
        ("list_documents", set()),
        ("list_tags", set()),
        ("search", {"query"}),
        ("search_by_tag", {"query", "tags"}),
        ("search_by_document", {"query", "documents"}),
        ("get_document_summary", {"document"}),
        ("fetch_chunk_context", {"chunk_id"}),
    ],
)
def test_required_parameters(tools: dict, tool_name: str, required: set[str]) -> None:
    """The filter is required on the narrowing tools -- that is what distinguishes them."""
    schema = tools[tool_name].input_schema
    assert set(schema.get("required", [])) == required


def test_every_tool_has_a_substantial_description(tools: dict) -> None:
    for name, tool in tools.items():
        description = (tool.description or "").strip()
        assert len(description) > 200, f"{name} has a thin description"
        # Docstring indentation must not leak into what the model reads.
        assert "\n    " not in description, f"{name} description is not dedented"


def test_search_tools_redirect_to_their_siblings(tools: dict) -> None:
    """Each search tool must name the alternative to use when it is the wrong choice."""
    assert "search_by_tag" in tools["search"].description
    assert "search_by_document" in tools["search"].description
    assert "search" in tools["search_by_tag"].description
    assert "list_tags" in tools["search_by_tag"].description
    assert "search" in tools["search_by_document"].description
    assert "search_by_tag" in tools["list_tags"].description
    assert "search" in tools["list_documents"].description


def test_every_parameter_is_documented(tools: dict) -> None:
    for name, tool in tools.items():
        for parameter, spec in tool.input_schema.get("properties", {}).items():
            assert spec.get("description"), f"{name}.{parameter} has no description"


def test_numeric_parameters_are_bounded(tools: dict) -> None:
    """Bounds stop a model inventing top_k=500 and blowing up its own context."""
    expected = {
        ("search", "top_k"): (1, 20),
        ("search_by_tag", "top_k"): (1, 20),
        ("search_by_document", "top_k"): (1, 20),
        ("list_documents", "limit"): (1, 100),
        ("fetch_chunk_context", "before"): (0, 5),
        ("fetch_chunk_context", "after"): (0, 5),
    }
    for (tool_name, parameter), (low, high) in expected.items():
        spec = tools[tool_name].input_schema["properties"][parameter]
        assert spec["minimum"] == low
        assert spec["maximum"] == high


def test_enum_parameters_are_closed(tools: dict) -> None:
    assert tools["search_by_tag"].input_schema["properties"]["tag_match"]["enum"] == [
        "any",
        "all",
    ]
    assert tools["list_documents"].input_schema["properties"]["sort"]["enum"] == [
        "recent",
        "name",
    ]


def test_search_tools_share_one_output_schema(tools: dict) -> None:
    """One result shape across the three search tools: the agent learns it once."""
    schemas = [
        tools[name].output_schema for name in ("search", "search_by_tag", "search_by_document")
    ]
    assert schemas[0] == schemas[1] == schemas[2]
    assert schemas[0] is not None


def test_search_results_carry_the_ids_needed_for_follow_up_calls(tools: dict) -> None:
    schema = tools["search"].output_schema
    hit = schema["$defs"]["SearchHit"]["properties"]
    for field in ("chunk_id", "document_id", "filename", "heading_path", "relevance", "text"):
        assert field in hit


def test_every_tool_is_annotated_read_only(tools: dict) -> None:
    """Nothing here mutates the knowledge base; saying so lets clients skip approval."""
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is True, name


def test_server_instructions_describe_the_workflow() -> None:
    from kb.mcp_server.server import SERVER_INSTRUCTIONS

    for tool_name in REQUIRED_BY_THE_BRIEF | ADDITIONAL:
        assert tool_name in SERVER_INSTRUCTIONS


def test_search_hit_exposes_absolute_similarity(tools: dict) -> None:
    """`relevance` is relative; an agent also needs an absolute signal to say "no answer"."""
    hit = tools["search"].output_schema["$defs"]["SearchHit"]["properties"]
    assert "similarity" in hit
    # The two scores must be documented as different things and cross-reference each
    # other, or the model will read `relevance` as a confidence score -- which it is not,
    # since the top hit always scores 1.0 even when nothing relevant exists.
    assert "not a confidence score" in hit["relevance"]["description"].lower()
    assert "`similarity`" in hit["relevance"]["description"]
    assert "absolute" in hit["similarity"]["description"].lower()


def test_weak_results_warn_the_agent_not_to_answer() -> None:
    """A result set whose best absolute match is poor must say so, not just hand over text."""
    from kb.mcp_server.tools import WEAK_SIMILARITY, _quality_guidance
    from kb.retrieval.service import SearchResponse, SearchResult

    def hit(similarity: float) -> SearchResult:
        return SearchResult(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            filename="policy.md",
            document_title="Policy",
            tags=[],
            chunk_index=0,
            heading_path="Policy > 1",
            page=None,
            text="...",
            relevance=1.0,
            similarity=similarity,
            matched_by=["semantic"],
            truncated=False,
        )

    weak = SearchResponse(
        results=[hit(WEAK_SIMILARITY - 0.1)], documents_searched=3, total_candidates=3
    )
    strong = SearchResponse(results=[hit(0.82)], documents_searched=3, total_candidates=3)

    assert "WARNING" in _quality_guidance(weak, scoped=False)
    assert "general knowledge" in _quality_guidance(weak, scoped=False)
    assert "WARNING" not in _quality_guidance(strong, scoped=False)


def test_weak_scoped_results_suggest_widening_the_search() -> None:
    from kb.mcp_server.tools import _quality_guidance
    from kb.retrieval.service import SearchResponse, SearchResult

    result = SearchResult(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename="policy.md",
        document_title=None,
        tags=[],
        chunk_index=0,
        heading_path=None,
        page=None,
        text="...",
        relevance=1.0,
        similarity=0.1,
        matched_by=["keyword"],
        truncated=False,
    )
    guidance = _quality_guidance(
        SearchResponse(results=[result], documents_searched=1, total_candidates=1), scoped=True
    )
    assert "`search`" in guidance
