"""Chat endpoints: the natural-language front door to the knowledge base."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from kb.agent.catalog import MODELS, default_model, is_available
from kb.agent.mcp_client import probe
from kb.agent.service import ChatService
from kb.api.schemas import ChatHealthOut, ChatModelOut, ChatModelsOut
from kb.auth.deps import get_current_user
from kb.config import get_settings
from kb.db.models import User, UserRole
from kb.db.repositories import UserRepository
from kb.db.session import get_db
from kb.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

_service = ChatService()


class ChatMessageIn(BaseModel):
    """One turn of the conversation as sent by the browser."""

    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """A chat turn: the full visible conversation plus the chosen model."""

    model: str = Field(description="Model id from GET /chat/models.")
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=50)
    api_key: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Personal API key for the model's provider, from the browser's Settings page. "
            "Used for this turn only; never persisted or logged server-side."
        ),
    )


@router.get("/models", response_model=ChatModelsOut)
def list_models() -> ChatModelsOut:
    """List the selectable models and whether each one is usable.

    Unavailable models are still returned, with the environment variable that
    would enable them, so the UI can grey them out and explain why rather than
    hiding a model an operator thought they had configured.
    """
    current = default_model()
    return ChatModelsOut(
        models=[
            ChatModelOut(
                id=model.id,
                label=model.label,
                provider=model.provider,
                provider_label=model.provider_label,
                description=model.description,
                available=is_available(model),
                requires_env_var=model.env_var,
            )
            for model in MODELS
        ],
        default=current.id if current else None,
    )


@router.get("/health", response_model=ChatHealthOut)
async def chat_health() -> ChatHealthOut:
    """Check that the assistant can actually reach the knowledge base.

    Kept off ``/healthz`` on purpose: that endpoint backs the container
    healthcheck and must not depend on a service that starts after it. This one
    is what you call to diagnose "the chat cannot find anything".
    """
    try:
        tools = await probe()
        return ChatHealthOut(
            mcp_server="ok",
            mcp_server_url=get_settings().mcp_server_url,
            tools=tools,
            models_available=sum(1 for model in MODELS if is_available(model)),
        )
    except Exception as exc:
        return ChatHealthOut(
            mcp_server=f"error: {exc}",
            mcp_server_url=get_settings().mcp_server_url,
            tools=[],
            models_available=sum(1 for model in MODELS if is_available(model)),
        )


@router.post("")
async def chat(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Answer a question, streaming tokens and tool activity as Server-Sent Events.

    Streaming is not decoration: a turn that runs several searches takes many
    seconds, and the tool-call events are what let the UI show the agent choosing
    between the search tools while the user waits.

    Quota: a turn that supplies its own ``api_key`` never touches the free-run
    counter. The admin role (see ``kb.auth.bootstrap``) is exempt from the counter
    entirely -- it always runs on the operator's own provider keys, which is the
    account meant to be handed out for demos. Everyone else spends one free run per
    turn that relies on the operator's keys, and is rejected once exhausted rather
    than silently charged to the operator's account.
    """
    using_own_key = bool((request.api_key or "").strip())
    needs_quota = user.role != UserRole.ADMIN and not using_own_key
    if needs_quota and not UserRepository(db).decrement_free_run(user.id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                "Your free trial runs are used up. Add your own API key in Settings "
                "to keep chatting."
            ),
        )

    async def event_stream() -> AsyncIterator[str]:
        messages = [message.model_dump() for message in request.messages]
        async for event in _service.stream(
            model_id=request.model, messages=messages, client_api_key=request.api_key
        ):
            yield f"data: {json.dumps(asdict(event))}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx and similar proxies buffering the stream into one lump.
            "X-Accel-Buffering": "no",
        },
    )
