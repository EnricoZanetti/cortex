"""Provider adapters: one agent loop per LLM vendor.

Each adapter does the same three things in that vendor's dialect:

1. translate the MCP tool list into the vendor's tool-definition format,
2. run the request/tool-call/result loop until the model stops asking for tools,
3. emit the shared events from :mod:`kb.agent.events` as it goes.

The MCP tool *schemas are passed through untouched*. The descriptions written for
the MCP server are the descriptions every model sees, which is what keeps the
tool-selection behaviour consistent across vendors instead of drifting into three
separately-tuned prompt variants.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, cast

from kb.agent.events import (
    ChatEvent,
    TextDelta,
    ToolCall,
    ToolResult,
    TurnDone,
    citations_from,
)
from kb.agent.mcp_client import McpTool, McpToolResult
from kb.config import get_settings
from kb.logging import get_logger

logger = get_logger(__name__)

#: Executes one tool call. Supplied by the chat service, which owns the MCP session.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[McpToolResult]]


class ChatProvider(Protocol):
    """What every vendor adapter implements.

    Declared as a plain ``def`` returning an ``AsyncIterator``: the adapters are
    async *generators*, so calling ``run`` returns the iterator directly rather
    than a coroutine that must be awaited first.
    """

    def run(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        tools: list[McpTool],
        execute_tool: ToolExecutor,
    ) -> AsyncIterator[ChatEvent]:
        """Answer the latest user message, using tools as needed."""
        ...


def _tool_events(name: str, result: McpToolResult) -> ToolResult:
    citations, count, guidance = citations_from(result.structured)
    return ToolResult(
        name=name,
        is_error=result.is_error,
        result_count=count,
        citations=citations,
        guidance=guidance,
    )


class AnthropicProvider:
    """Claude models, via the Anthropic SDK's Messages API."""

    def __init__(self, api_key: str) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)

    async def run(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        tools: list[McpTool],
        execute_tool: ToolExecutor,
    ) -> AsyncIterator[ChatEvent]:
        settings = get_settings()
        tool_defs = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in tools
        ]
        history: list[dict[str, Any]] = [
            {"role": message["role"], "content": message["content"]} for message in messages
        ]

        for _ in range(settings.chat_max_tool_iterations):
            # Streaming keeps the connection alive through long tool-using turns
            # and lets the browser render tokens as they arrive.
            # Messages and tool definitions are assembled from MCP data at runtime,
            # so they cannot be checked against the SDK's TypedDicts statically.
            async with self._client.messages.stream(
                model=model,
                max_tokens=settings.chat_max_output_tokens,
                system=system,
                messages=cast(Any, history),
                tools=cast(Any, tool_defs),
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield TextDelta(text=event.delta.text)
                response = await stream.get_final_message()

            history.append({"role": "assistant", "content": response.content})
            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                yield TurnDone(stop_reason=response.stop_reason)
                return

            tool_results: list[dict[str, Any]] = []
            for block in tool_uses:
                arguments = dict(block.input) if isinstance(block.input, dict) else {}
                yield ToolCall(name=block.name, arguments=arguments)
                result = await execute_tool(block.name, arguments)
                yield _tool_events(block.name, result)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result.text,
                        "is_error": result.is_error,
                    }
                )
            history.append({"role": "user", "content": tool_results})

        yield TurnDone(stop_reason="max_tool_iterations")


class OpenAIProvider:
    """GPT models, via the OpenAI Chat Completions API."""

    def __init__(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)

    async def run(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        tools: list[McpTool],
        execute_tool: ToolExecutor,
    ) -> AsyncIterator[ChatEvent]:
        settings = get_settings()
        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]
        history: list[dict[str, Any]] = [{"role": "system", "content": system}]
        history.extend({"role": m["role"], "content": m["content"]} for m in messages)

        for _ in range(settings.chat_max_tool_iterations):
            # As above: assembled at runtime from MCP tool schemas.
            response = await self._client.chat.completions.create(
                model=model,
                max_completion_tokens=settings.chat_max_output_tokens,
                messages=cast(Any, history),
                tools=cast(Any, tool_defs),
            )
            choice = response.choices[0]
            message = choice.message

            if message.content:
                yield TextDelta(text=message.content)

            if not message.tool_calls:
                yield TurnDone(stop_reason=choice.finish_reason)
                return

            history.append(message.model_dump(exclude_none=True))
            for call in message.tool_calls:
                # The SDK's tool-call union also covers OpenAI's "custom" tool type,
                # which has no .function; we only ever declare function tools.
                if call.type != "function":
                    continue
                name = call.function.name
                arguments = _safe_json(call.function.arguments)
                yield ToolCall(name=name, arguments=arguments)
                result = await execute_tool(name, arguments)
                yield _tool_events(name, result)
                history.append({"role": "tool", "tool_call_id": call.id, "content": result.text})

        yield TurnDone(stop_reason="max_tool_iterations")


class GoogleProvider:
    """Gemini models, via the google-genai SDK."""

    def __init__(self, api_key: str) -> None:
        from google import genai

        self._genai = genai
        self._client = genai.Client(api_key=api_key)

    async def run(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        tools: list[McpTool],
        execute_tool: ToolExecutor,
    ) -> AsyncIterator[ChatEvent]:
        from google.genai import types as genai_types

        settings = get_settings()
        declarations = [
            genai_types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters_json_schema=tool.input_schema,
            )
            for tool in tools
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=settings.chat_max_output_tokens,
            tools=[genai_types.Tool(function_declarations=declarations)],
            # The SDK would otherwise run the tool loop itself; we drive it so the
            # UI can show each call as it happens.
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents: list[Any] = [
            genai_types.Content(
                role="user" if message["role"] == "user" else "model",
                parts=[genai_types.Part(text=message["content"])],
            )
            for message in messages
        ]

        for _ in range(settings.chat_max_tool_iterations):
            response = await self._client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
            candidate = response.candidates[0] if response.candidates else None
            parts = list(candidate.content.parts or []) if candidate and candidate.content else []

            for part in parts:
                if part.text:
                    yield TextDelta(text=part.text)

            # A function call without a name is unusable; treat it as absent rather
            # than dispatching an empty tool name at the MCP server.
            calls = [
                part.function_call
                for part in parts
                if part.function_call is not None and part.function_call.name
            ]
            if not calls:
                yield TurnDone(stop_reason=str(candidate.finish_reason) if candidate else None)
                return

            contents.append(genai_types.Content(role="model", parts=parts))
            response_parts = []
            for call in calls:
                name = call.name or ""
                arguments = dict(call.args or {})
                yield ToolCall(name=name, arguments=arguments)
                result = await execute_tool(name, arguments)
                yield _tool_events(name, result)
                response_parts.append(
                    genai_types.Part.from_function_response(
                        name=name, response={"result": result.text}
                    )
                )
            contents.append(genai_types.Content(role="user", parts=response_parts))

        yield TurnDone(stop_reason="max_tool_iterations")


def _safe_json(raw: str | None) -> dict[str, Any]:
    """Parse tool arguments defensively; a malformed blob must not kill the turn."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("tool_arguments_unparseable", raw=raw[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_provider(provider: str, api_key: str) -> ChatProvider:
    """Instantiate the adapter for a provider name."""
    builders: dict[str, Callable[[str], ChatProvider]] = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "google": GoogleProvider,
    }
    try:
        return builders[provider](api_key)
    except KeyError as exc:
        raise ValueError(f"No adapter for provider {provider!r}.") from exc
