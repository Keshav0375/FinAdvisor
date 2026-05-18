from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from src.agent.orchestrator import FinAdvisorAgent
from src.agent.types import StreamEvent
from src.api.schemas import ChatRequest
from src.auth.jwt import get_current_user
from src.auth.models import UserClaims
from src.dependencies import get_agent, get_redactor
from src.pii.fallback import RegexRedactor

log = structlog.get_logger()

router = APIRouter()


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: UserClaims = Depends(get_current_user),
    agent: FinAdvisorAgent = Depends(get_agent),
    redactor: RegexRedactor = Depends(get_redactor),
) -> EventSourceResponse:
    log.info(
        "chat_stream_start",
        user=user.sub,
        message_length=len(request.message),
        conversation_id=request.conversation_id,
    )

    async def event_generator() -> AsyncGenerator[dict[str, str], None]:
        async for event in agent.run(request.message, user):
            yield _format_sse_event(event, redactor)

        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


def _format_sse_event(event: StreamEvent, redactor: RegexRedactor) -> dict[str, str]:
    if event.type == "text":
        redacted = redactor.redact(event.content or "")
        return {
            "event": "message",
            "data": json.dumps({"type": "text", "content": redacted.redacted_text}),
        }

    if event.type == "tool_call" and event.tool_call is not None:
        return {
            "event": "tool",
            "data": json.dumps(
                {
                    "tool": event.tool_call.tool_name,
                    "input": event.tool_call.tool_input,
                    "tool_use_id": event.tool_call.tool_use_id,
                }
            ),
        }

    if event.type == "tool_result":
        return {
            "event": "tool_result",
            "data": json.dumps({"content": event.content}),
        }

    if event.type == "citation" and event.citation is not None:
        return {
            "event": "citation",
            "data": event.citation.model_dump_json(),
        }

    if event.type == "error":
        return {
            "event": "error",
            "data": json.dumps({"message": event.content}),
        }

    return {
        "event": "message",
        "data": json.dumps({"type": event.type, "content": event.content}),
    }
