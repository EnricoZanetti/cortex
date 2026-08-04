"""The chat assistant.

The vendor SDK call is the one thing not exercised here: a stub provider stands in
for the LLM, which lets the rest of the turn run for real against the live MCP
server. That covers the parts most likely to break: the MCP session, the bearer
auth, tool dispatch, citation extraction and the event stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from kb.agent import catalog
from kb.agent.events import (
    TextDelta,
    ToolCall,
    ToolResult,
    TurnDone,
    TurnError,
    citations_from,
)
from kb.agent.mcp_client import McpTool
from kb.agent.providers import ToolExecutor
from kb.agent.service import ChatService


class StubProvider:
    """A scripted model: calls the named tools once, then answers."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], answer: str = "Done.") -> None:
        self._calls = calls
        self._answer = answer
        self.seen_tools: list[str] = []
        self.seen_system: str = ""

    async def run(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        tools: list[McpTool],
        execute_tool: ToolExecutor,
    ) -> AsyncIterator[Any]:
        self.seen_tools = [tool.name for tool in tools]
        self.seen_system = system
        for name, arguments in self._calls:
            yield ToolCall(name=name, arguments=arguments)
            result = await execute_tool(name, arguments)
            citations, count, guidance = citations_from(result.structured)
            yield ToolResult(
                name=name,
                is_error=result.is_error,
                result_count=count,
                citations=citations,
                guidance=guidance,
            )
        yield TextDelta(text=self._answer)
        yield TurnDone(stop_reason="end_turn")


# --- catalogue ------------------------------------------------------------------


class TestCatalogue:
    def test_every_model_maps_to_a_known_provider(self) -> None:
        for model in catalog.MODELS:
            assert model.provider in catalog.PROVIDER_ENV_VAR
            assert model.env_var.endswith("_API_KEY")

    def test_model_ids_are_unique(self) -> None:
        ids = [model.id for model in catalog.MODELS]
        assert len(ids) == len(set(ids))

    def test_providers_span_the_major_vendors(self) -> None:
        """The brief asks for a choice between popular models, not one vendor."""
        assert {model.provider for model in catalog.MODELS} == {"anthropic", "openai", "google"}

    def test_unknown_model_lists_the_valid_ones(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(catalog.ModelNotAvailableError) as excinfo:
            catalog.resolve("gpt-9-ultra")
        assert "claude-opus-5" in str(excinfo.value)

    def test_unconfigured_model_names_the_env_var_to_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator must be told exactly what is missing, not just 'unavailable'."""
        monkeypatch.setattr(catalog, "provider_api_key", lambda provider: None)
        with pytest.raises(catalog.ModelNotAvailableError) as excinfo:
            catalog.resolve("claude-opus-5")
        assert "ANTHROPIC_API_KEY" in str(excinfo.value)

    def test_default_is_the_first_configured_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            catalog, "provider_api_key", lambda provider: "k" if provider == "google" else None
        )
        default = catalog.default_model()
        assert default is not None
        assert default.provider == "google"

    def test_default_is_none_when_nothing_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(catalog, "provider_api_key", lambda provider: None)
        assert catalog.default_model() is None


# --- citations -------------------------------------------------------------------


class TestCitations:
    def test_extracts_source_fields_from_a_search_result(self) -> None:
        citations, count, guidance = citations_from(
            {
                "results": [
                    {
                        "filename": "aml_policy.md",
                        "heading_path": "AML > 4. Reporting",
                        "page": None,
                        "document_id": "doc-1",
                        "chunk_id": "chunk-1",
                        "relevance": 1.0,
                    }
                ],
                "guidance": "Ground your answer in these passages.",
            }
        )
        assert count == 1
        assert citations[0]["filename"] == "aml_policy.md"
        assert guidance is not None

    def test_non_search_tools_yield_no_citations(self) -> None:
        citations, count, _ = citations_from({"tags": [{"tag": "hr"}], "total_tags": 1})
        assert citations == []
        assert count is None

    def test_missing_structured_output_is_safe(self) -> None:
        assert citations_from(None) == ([], None, None)


# --- the turn --------------------------------------------------------------------


class TestChatTurn:
    async def test_unknown_model_yields_an_error_event_not_an_exception(self) -> None:
        """The caller is an SSE stream; a raised exception would truncate the page."""
        events = [
            event
            async for event in ChatService().stream(
                model_id="not-a-model", messages=[{"role": "user", "content": "hi"}]
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], TurnError)
        assert "not-a-model" in events[0].message

    async def test_unreachable_mcp_server_is_reported_as_an_error_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(catalog, "provider_api_key", lambda provider: "test-key")
        monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:1/mcp")
        from kb.config import get_settings

        get_settings.cache_clear()
        try:
            events = [
                event
                async for event in ChatService().stream(
                    model_id="claude-opus-5", messages=[{"role": "user", "content": "hi"}]
                )
            ]
        finally:
            get_settings.cache_clear()
        assert any(isinstance(event, TurnError) for event in events)


@pytest.mark.integration
class TestChatAgainstLiveMcpServer:
    """Runs the real agent loop against the running MCP server."""

    @pytest.fixture
    def service_and_stub(self, monkeypatch: pytest.MonkeyPatch) -> tuple[ChatService, StubProvider]:
        stub = StubProvider(
            calls=[("search", {"query": "suspicious transaction reporting", "top_k": 2})]
        )
        monkeypatch.setattr(catalog, "provider_api_key", lambda provider: "test-key")
        return ChatService(provider_factory=lambda provider, key: stub), stub

    async def test_turn_calls_a_tool_and_returns_citations(
        self, service_and_stub: tuple[ChatService, StubProvider]
    ) -> None:
        service, _ = service_and_stub
        events = [
            event
            async for event in service.stream(
                model_id="claude-opus-5",
                messages=[{"role": "user", "content": "How fast must I report?"}],
            )
        ]
        if any(isinstance(event, TurnError) for event in events):
            pytest.skip("MCP server is not reachable")

        assert any(isinstance(event, ToolCall) for event in events)
        results = [event for event in events if isinstance(event, ToolResult)]
        assert results
        assert not results[0].is_error
        assert results[0].citations, "a search must return citable sources"
        assert results[0].citations[0]["filename"]
        assert any(isinstance(event, TextDelta) for event in events)
        assert isinstance(events[-1], TurnDone)

    async def test_the_model_receives_the_mcp_servers_own_tool_definitions(
        self, service_and_stub: tuple[ChatService, StubProvider]
    ) -> None:
        """No second, divergent copy of the tool descriptions exists for the chat."""
        service, stub = service_and_stub
        events = [
            event
            async for event in service.stream(
                model_id="claude-opus-5", messages=[{"role": "user", "content": "hello"}]
            )
        ]
        if any(isinstance(event, TurnError) for event in events):
            pytest.skip("MCP server is not reachable")
        assert {"search", "search_by_tag", "search_by_document"} <= set(stub.seen_tools)
        assert len(stub.seen_tools) == 7

    async def test_an_unresolvable_document_returns_recoverable_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wrong document name must come back as guidance, not a dead end.

        This is the behaviour that lets an agent correct itself in one turn
        instead of telling the user the answer does not exist.
        """
        monkeypatch.setattr(catalog, "provider_api_key", lambda provider: "test-key")
        stub = StubProvider(
            calls=[("search_by_document", {"query": "reporting", "documents": ["aml_polcy"]})]
        )
        service = ChatService(provider_factory=lambda provider, key: stub)
        events = [
            event
            async for event in service.stream(
                model_id="claude-opus-5", messages=[{"role": "user", "content": "hi"}]
            )
        ]
        if any(isinstance(event, TurnError) for event in events):
            pytest.skip("MCP server is not reachable")
        results = [event for event in events if isinstance(event, ToolResult)]
        assert results
        assert not results[0].is_error, "an unknown name is not a tool failure"
        assert results[0].guidance is not None
        assert "list_documents" in results[0].guidance

    async def test_invalid_arguments_surface_as_an_error_the_model_can_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A schema violation is reported back, not raised: the model can retry."""
        monkeypatch.setattr(catalog, "provider_api_key", lambda provider: "test-key")
        # `query` has min_length=2 in the tool schema.
        stub = StubProvider(calls=[("search", {"query": "x"})])
        service = ChatService(provider_factory=lambda provider, key: stub)
        events = [
            event
            async for event in service.stream(
                model_id="claude-opus-5", messages=[{"role": "user", "content": "hi"}]
            )
        ]
        if any(isinstance(event, TurnError) for event in events):
            pytest.skip("MCP server is not reachable")
        results = [event for event in events if isinstance(event, ToolResult)]
        assert results
        assert results[0].is_error
        # The turn continues to completion despite the bad call.
        assert isinstance(events[-1], TurnDone)


class TestErrorMessages:
    """Provider failures must reach the user as something they can act on."""

    def test_nested_exception_groups_are_unwrapped(self) -> None:
        """The MCP transport wraps errors in task groups; the leaf is what matters."""
        from kb.agent.service import _leaf_causes

        root = ValueError("the real problem")
        nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [root])])
        assert _leaf_causes(nested) == [root]

    def test_quota_exhaustion_is_not_reported_as_a_rate_limit(self) -> None:
        """Both arrive as HTTP 429, but 'add credits' is not 'retry shortly'."""
        from kb.agent.service import _friendly_error

        model = catalog.MODELS_BY_ID["gpt-5.6-luna"]
        exhausted = RuntimeError(
            "Error code: 429 - {'message': 'You have no credits remaining.', "
            "'code': 'credit_balance_exhausted'}"
        )
        message = _friendly_error(model, exhausted)
        assert "quota" in message.lower()
        assert "try again shortly" not in message.lower()

    def test_a_buried_provider_error_still_produces_a_useful_message(self) -> None:
        """Regression: this used to surface as 'unhandled errors in a TaskGroup'."""
        from kb.agent.service import _friendly_error

        model = catalog.MODELS_BY_ID["gpt-5.6-luna"]
        buried = BaseExceptionGroup(
            "unhandled errors in a TaskGroup",
            [BaseExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("401 api key")])],
        )
        message = _friendly_error(model, buried)  # type: ignore[arg-type]
        assert "TaskGroup" not in message
        assert "OPENAI_API_KEY" in message
