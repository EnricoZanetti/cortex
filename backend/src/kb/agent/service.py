"""The chat service: one question in, a stream of events out.

Holds the MCP session open for the duration of a turn, hands the tool list to the
chosen provider, and lets the provider drive the loop. The system prompt lives
here rather than in each adapter so all models are given the same instructions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from kb.agent import catalog
from kb.agent.catalog import ModelSpec
from kb.agent.events import ChatEvent, TurnError
from kb.agent.mcp_client import KnowledgeBaseMcpClient, McpToolResult
from kb.agent.providers import ChatProvider, build_provider
from kb.logging import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """\
You are the assistant for a company knowledge base of internal documents: compliance
policies, product manuals, onboarding guides, HR and FAQ material. Employees ask you
questions in natural language and you answer from the documents.

Ground every factual claim in the knowledge base tools. The tools tell you, in their
own descriptions, when each one is the right choice: read them and follow that guidance
rather than guessing. In short: start with `search`; narrow with `search_by_tag` or
`search_by_document` when the question clearly belongs to one topic or document; and
call `list_tags` before filtering by a tag you are not certain exists.

Cite your sources. After a claim, name the document and the section it came from, using
the `filename` and `heading_path` on each result.

If the tools return nothing relevant, say the knowledge base does not cover it. Do not
fall back to general knowledge without saying so plainly. Each tool response carries a
`guidance` field telling you what to try next when results are weak or empty; act on it
before concluding there is no answer.

Answer in prose, concisely. Lead with the answer, then the supporting detail.
"""


#: Builds the adapter for a provider. Injectable so the agent loop, the MCP tool
#: execution and the event stream can be tested without calling a vendor API.
ProviderFactory = Callable[[str, str], ChatProvider]


class ChatService:
    """Runs one chat turn end to end."""

    def __init__(self, provider_factory: ProviderFactory | None = None) -> None:
        self._build_provider: ProviderFactory = provider_factory or build_provider

    async def stream(
        self,
        *,
        model_id: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[ChatEvent]:
        """Yield events for a turn. Never raises: failures arrive as an error event.

        The caller is an SSE response; an exception escaping here would truncate
        the stream with no explanation on the page, so every failure is converted
        into a `TurnError` the UI can render.
        """
        try:
            model, api_key = catalog.resolve(model_id)
        except Exception as exc:
            yield TurnError(message=str(exc))
            return

        try:
            async with KnowledgeBaseMcpClient() as mcp:
                tools = await mcp.list_tools()
                if not tools:
                    yield TurnError(
                        message=(
                            "The knowledge base exposed no tools. Check that the MCP server "
                            "is running and reachable at the configured MCP_SERVER_URL."
                        )
                    )
                    return

                async def execute_tool(name: str, arguments: dict[str, Any]) -> McpToolResult:
                    logger.info("chat_tool_call", tool=name, model=model.id)
                    return await mcp.call_tool(name, arguments)

                provider = self._build_provider(model.provider, api_key)
                async for event in provider.run(
                    model=model.id,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=tools,
                    execute_tool=execute_tool,
                ):
                    yield event
        except Exception as exc:
            logger.exception("chat_turn_failed", model=model.id)
            yield TurnError(message=_friendly_error(model, exc))


def _leaf_causes(exc: BaseException) -> list[BaseException]:
    """Flatten nested ``ExceptionGroup``s down to the real failures.

    The MCP transport runs on anyio task groups, so an error raised inside the
    session, including a provider error from the model call, comes back wrapped
    in one or more ``ExceptionGroup``s. Reporting the wrapper gives the user
    "unhandled errors in a TaskGroup", which tells them nothing.
    """
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _leaf_causes(inner)]
    return [exc]


def _friendly_error(model: ModelSpec, exc: Exception) -> str:
    """Turn provider exceptions into something an employee can act on."""
    causes = _leaf_causes(exc)
    # Prefer the first leaf: with a single failing call there is exactly one.
    root = causes[0] if causes else exc
    text = str(root)
    lowered = text.lower()
    if "connect" in lowered and "mcp" not in lowered:
        return (
            f"Could not reach {model.provider_label}. Check network access and that "
            f"{model.env_var} is valid."
        )
    if "authentication" in lowered or "401" in text or "api key" in lowered:
        return f"{model.provider_label} rejected the API key. Check {model.env_var}."
    # Check quota before rate limiting: providers report an exhausted balance as a
    # 429 too, and "add credits" is a very different action from "retry shortly".
    if "credit" in lowered or "quota" in lowered or "billing" in lowered:
        return (
            f"The {model.provider_label} account has no remaining quota for "
            f"{model.label}. Add credits, or pick a model from another provider."
        )
    if "rate limit" in lowered or "429" in text:
        return f"{model.provider_label} is rate limiting this key. Try again shortly."
    return f"The assistant could not complete this request: {text}"
