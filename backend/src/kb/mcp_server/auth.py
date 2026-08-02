"""Authentication for the MCP endpoint.

A pre-shared bearer token, enforced by ASGI middleware in front of the MCP transport.

Why a static token rather than full OAuth: the client here is a trusted internal agent
deployment, not an end-user browser flow. A bearer token is what every MCP client
(Claude Code, Claude Desktop, custom agents, curl) can send today with a single header,
and it is the mechanism the assignment names. The per-user authorisation story -- scoping
which documents a given employee's agent may retrieve -- is called out in the README as
the next step, and would slot in here as a token-to-principal lookup.
"""

from __future__ import annotations

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from kb.logging import get_logger

logger = get_logger(__name__)

BEARER_PREFIX = "bearer "


class BearerAuthMiddleware:
    """Reject requests to protected paths without a valid credential.

    Accepts either ``Authorization: Bearer <token>`` (preferred, and what MCP clients
    send natively) or ``X-API-Key: <token>`` for convenience with tools like curl/Postman.
    """

    def __init__(self, app: ASGIApp, *, api_key: str, protected_prefix: str = "/mcp") -> None:
        self.app = app
        self._api_key = api_key
        self._protected_prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_protected(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        presented = self._extract_token(headers)
        if presented is None or not hmac.compare_digest(presented, self._api_key):
            # Never log the presented value: a mistyped key is still a secret.
            logger.warning(
                "mcp_auth_rejected",
                path=scope.get("path"),
                had_credential=presented is not None,
            )
            await self._unauthorized(scope, receive, send, presented is not None)
            return

        await self.app(scope, receive, send)

    def _is_protected(self, path: str) -> bool:
        return path.startswith(self._protected_prefix)

    @staticmethod
    def _extract_token(headers: dict[str, str]) -> str | None:
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith(BEARER_PREFIX):
            token = authorization[len(BEARER_PREFIX) :].strip()
            if token:
                return token
        api_key = headers.get("x-api-key", "").strip()
        return api_key or None

    @staticmethod
    async def _unauthorized(
        scope: Scope, receive: Receive, send: Send, had_credential: bool
    ) -> None:
        detail = (
            "Invalid API key."
            if had_credential
            else "Missing credentials. Send 'Authorization: Bearer <MCP_API_KEY>'."
        )
        response = JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "detail": detail},
            headers={"WWW-Authenticate": 'Bearer realm="mcp"'},
        )
        await response(scope, receive, send)
