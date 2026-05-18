from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.agent.orchestrator import FinAdvisorAgent
from src.agent.types import StreamEvent, ToolCallEvent
from src.api.chat import _format_sse_event
from src.auth.models import UserClaims
from src.dependencies import get_agent, get_redactor
from src.main import app
from src.pii.fallback import RegexRedactor


def test_format_text_event() -> None:
    event = StreamEvent(type="text", content="Bond funds are suitable [1].")
    redactor = RegexRedactor()
    result = _format_sse_event(event, redactor)

    assert result["event"] == "message"
    data = json.loads(result["data"])
    assert data["type"] == "text"
    assert data["content"] == "Bond funds are suitable [1]."


def test_format_text_event_redacts_pii() -> None:
    event = StreamEvent(type="text", content="Contact john@example.com for details.")
    redactor = RegexRedactor()
    result = _format_sse_event(event, redactor)

    data = json.loads(result["data"])
    assert "[EMAIL_REDACTED]" in data["content"]
    assert "john@example.com" not in data["content"]


def test_format_tool_call_event() -> None:
    event = StreamEvent(
        type="tool_call",
        tool_call=ToolCallEvent(
            tool_name="search_firm_kb",
            tool_input={"query": "bond funds"},
            tool_use_id="toolu_123",
        ),
    )
    redactor = RegexRedactor()
    result = _format_sse_event(event, redactor)

    assert result["event"] == "tool"
    data = json.loads(result["data"])
    assert data["tool"] == "search_firm_kb"
    assert data["input"] == {"query": "bond funds"}


def test_format_tool_result_event() -> None:
    event = StreamEvent(type="tool_result", content='{"results": []}')
    redactor = RegexRedactor()
    result = _format_sse_event(event, redactor)

    assert result["event"] == "tool_result"
    data = json.loads(result["data"])
    assert data["content"] == '{"results": []}'


def test_format_error_event() -> None:
    event = StreamEvent(type="error", content="Max iterations reached.")
    redactor = RegexRedactor()
    result = _format_sse_event(event, redactor)

    assert result["event"] == "error"
    data = json.loads(result["data"])
    assert data["message"] == "Max iterations reached."


@pytest.mark.asyncio
async def test_chat_stream_endpoint_returns_sse() -> None:
    async def mock_agent_run(
        query: str, user: UserClaims, **kwargs: object
    ) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            type="tool_call",
            tool_call=ToolCallEvent(
                tool_name="search_firm_kb",
                tool_input={"query": "bonds"},
                tool_use_id="toolu_1",
            ),
        )
        yield StreamEvent(type="tool_result", content='{"results": []}')
        yield StreamEvent(type="text", content="Bond funds are suitable [1].")

    mock_agent = MagicMock(spec=FinAdvisorAgent)
    mock_agent.run = mock_agent_run

    mock_redactor = RegexRedactor()

    app.dependency_overrides[get_agent] = lambda: mock_agent
    app.dependency_overrides[get_redactor] = lambda: mock_redactor

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/chat/stream",
                json={"message": "Tell me about bonds"},
                headers={
                    "X-User-Id": "sarah_chen",
                    "Content-Type": "application/json",
                },
            )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        lines = resp.text.strip().split("\n")
        events = []
        for line in lines:
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())

        assert "tool" in events
        assert "tool_result" in events
        assert "message" in events
        assert "done" in events
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_stream_requires_auth() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/chat/stream",
            json={"message": "test"},
        )
    assert resp.status_code == 401
