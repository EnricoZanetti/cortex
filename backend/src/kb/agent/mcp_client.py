"""The chat assistant's connection to the knowledge base.

The assistant talks to the MCP server the same way Claude Desktop or any other
client would: over Streamable HTTP, authenticated with the bearer token, calling
`tools/list` and `tools/call`. It deliberately does *not* import the tool
functions directly.

That costs a network hop, and it is worth it: the chat feature then exercises the
exact surface external agents use, so a badly worded tool description or a broken
auth header shows up in our own UI before a customer finds it. It also means the
assistant can be pointed at a remote deployment by changing one URL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from kb.config import get_settings
from kb.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class McpTool:
    """A tool as advertised by the MCP server, in provider-neutral form."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class McpToolResult:
    """The outcome of one tool call."""

    text: str
    """Result rendered as text; this is what goes back into the model's context."""

    structured: dict[str, Any] | None
    """The tool's structured output, used by the UI to render citations."""

    is_error: bool


class KnowledgeBaseMcpClient:
    """A short-lived MCP session, opened for the duration of one chat turn.

    Sessions are not pooled. The server runs stateless, a turn lasts seconds, and
    a fresh session per turn means a restarted MCP server never leaves the chat
    holding a dead connection.
    """

    def __init__(self, url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.url = url or settings.mcp_server_url
        self._api_key = api_key or settings.mcp_api_key.get_secret_value()
        self._client: Client | None = None
        self._http: httpx2.AsyncClient | None = None

    async def __aenter__(self) -> KnowledgeBaseMcpClient:
        # The transport takes a pre-built HTTP client, which is where the bearer
        # token goes; there is no separate auth parameter.
        self._http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx2.Timeout(60.0, connect=10.0),
        )
        transport = streamable_http_client(self.url, http_client=self._http)
        self._client = await Client(transport, raise_exceptions=True).__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            if self._client is not None:
                await self._client.__aexit__(*exc)  # type: ignore[arg-type]
        finally:
            if self._http is not None:
                await self._http.aclose()
            self._client = None
            self._http = None

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("MCP client used outside its context manager.")
        return self._client

    async def list_tools(self) -> list[McpTool]:
        """Fetch the server's tool definitions, descriptions included.

        These are handed to the model verbatim. The tool-design work in the MCP
        server is therefore exactly what the chat assistant sees; there is no
        second, divergent set of descriptions maintained for the UI.
        """
        listing = await self.client.list_tools()
        return [
            McpTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.input_schema),
            )
            for tool in listing.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        """Invoke a tool and flatten the result into text plus structured data.

        A failing tool is reported back to the model as an error *result* rather
        than raised: the tools are written to explain how to recover, and the
        model can act on that only if it sees the message.
        """
        try:
            result = await self.client.call_tool(name, arguments)
        except Exception as exc:
            logger.warning("mcp_tool_call_failed", tool=name, error=str(exc))
            return McpToolResult(
                text=f"The {name} tool could not be reached: {exc}", structured=None, is_error=True
            )

        structured = result.structured_content if hasattr(result, "structured_content") else None
        # Tool results may carry images, audio or resource links as well as text.
        # Only text goes into the model's context here; the structured payload
        # carries everything the UI needs.
        text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
        if not text and structured is not None:
            text = json.dumps(structured, default=str)
        return McpToolResult(
            text=text or "(the tool returned no content)",
            structured=structured,
            is_error=bool(result.is_error),
        )


async def probe() -> list[str]:
    """Return the names of the tools the MCP server currently advertises.

    Used by the health endpoint so a misconfigured MCP_SERVER_URL or MCP_API_KEY
    surfaces as a failing check rather than as a chat that silently has no tools.
    """
    async with KnowledgeBaseMcpClient() as client:
        return [tool.name for tool in await client.list_tools()]
