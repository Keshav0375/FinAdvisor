from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from src.agent.orchestrator import FinAdvisorAgent
from src.agent.types import CitationEvent, StreamEvent, ToolCallEvent
from src.auth.models import UserClaims
from src.dependencies import get_agent, get_redactor
from src.pii.fallback import RegexRedactor


def _mock_agent_with_citations() -> MagicMock:
    async def run(
        query: str, user: UserClaims, **kwargs: object
    ) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            type="tool_call",
            tool_call=ToolCallEvent(
                tool_name="search_firm_kb",
                tool_input={"query": "bond funds"},
                tool_use_id="toolu_abc",
            ),
        )
        yield StreamEvent(
            type="tool_result",
            content='{"results": [{"content": "Bond funds info"}]}',
        )
        yield StreamEvent(
            type="citation",
            citation=CitationEvent(
                index=1,
                source_title="US Product Sheet",
                regulatory_ref="SEC-17a-4",
                last_reviewed_at="2025-01-15",
            ),
        )
        yield StreamEvent(
            type="text",
            content="Bond funds are suitable for conservative portfolios [1].",
        )

    agent = MagicMock(spec=FinAdvisorAgent)
    agent.run = run
    return agent


def _mock_agent_multi_tool() -> MagicMock:
    async def run(
        query: str, user: UserClaims, **kwargs: object
    ) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            type="tool_call",
            tool_call=ToolCallEvent(
                tool_name="search_firm_kb",
                tool_input={"query": "suitability"},
                tool_use_id="toolu_1",
            ),
        )
        yield StreamEvent(type="tool_result", content='{"results": []}')
        yield StreamEvent(
            type="tool_call",
            tool_call=ToolCallEvent(
                tool_name="lookup_suitability_rule",
                tool_input={"product_type": "equity", "jurisdiction": "US"},
                tool_use_id="toolu_2",
            ),
        )
        yield StreamEvent(
            type="tool_result",
            content='{"rule": "Equities require risk assessment"}',
        )
        yield StreamEvent(
            type="text",
            content="Based on suitability rules, equities require risk assessment [1].",
        )

    agent = MagicMock(spec=FinAdvisorAgent)
    agent.run = run
    return agent


def _mock_agent_error() -> MagicMock:
    async def run(
        query: str, user: UserClaims, **kwargs: object
    ) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            type="error",
            content="Max reasoning iterations reached. Please simplify your query.",
        )

    agent = MagicMock(spec=FinAdvisorAgent)
    agent.run = run
    return agent


def _mock_agent_pii_leak() -> MagicMock:
    async def run(
        query: str, user: UserClaims, **kwargs: object
    ) -> AsyncGenerator[StreamEvent, None]:
        yield StreamEvent(
            type="text",
            content="Contact john.doe@example.com or call 555-123-4567 for details.",
        )

    agent = MagicMock(spec=FinAdvisorAgent)
    agent.run = run
    return agent


def _parse_sse_events(response_text: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in response_text.split("\n"):
        if line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current["data"] = line.split(":", 1)[1].strip()
        elif line.strip() == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


@pytest.mark.asyncio
async def test_full_stream_with_citations(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_with_citations
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={"message": "Tell me about bond funds"},
        headers={"X-User-Id": "sarah_chen"},
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    events = _parse_sse_events(resp.text)
    event_types = [e.get("event") for e in events]

    assert "tool" in event_types
    assert "tool_result" in event_types
    assert "citation" in event_types
    assert "message" in event_types
    assert "done" in event_types

    citation_event = next(e for e in events if e.get("event") == "citation")
    citation_data = json.loads(citation_event["data"])
    assert citation_data["index"] == 1
    assert citation_data["source_title"] == "US Product Sheet"
    assert citation_data["regulatory_ref"] == "SEC-17a-4"

    message_event = next(e for e in events if e.get("event") == "message")
    message_data = json.loads(message_event["data"])
    assert "[1]" in message_data["content"]


@pytest.mark.asyncio
async def test_multi_tool_dispatch_stream(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_multi_tool
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={"message": "What are the suitability rules for equities?"},
        headers={"X-User-Id": "sarah_chen"},
    )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    tool_events = [e for e in events if e.get("event") == "tool"]
    assert len(tool_events) == 2

    tool_names = [json.loads(e["data"])["tool"] for e in tool_events]
    assert "search_firm_kb" in tool_names
    assert "lookup_suitability_rule" in tool_names

    tool_result_events = [e for e in events if e.get("event") == "tool_result"]
    assert len(tool_result_events) == 2


@pytest.mark.asyncio
async def test_stream_error_event(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_error
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={"message": "Complex multi-step query"},
        headers={"X-User-Id": "sarah_chen"},
    )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    error_events = [e for e in events if e.get("event") == "error"]
    assert len(error_events) == 1
    error_data = json.loads(error_events[0]["data"])
    assert "Max reasoning iterations" in error_data["message"]


@pytest.mark.asyncio
async def test_pii_redacted_in_stream_output(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_pii_leak
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={"message": "Contact info for the advisor"},
        headers={"X-User-Id": "sarah_chen"},
    )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)

    message_event = next(e for e in events if e.get("event") == "message")
    message_data = json.loads(message_event["data"])

    assert "john.doe@example.com" not in message_data["content"]
    assert "555-123-4567" not in message_data["content"]
    assert "[EMAIL_REDACTED]" in message_data["content"]
    assert "[PHONE_REDACTED]" in message_data["content"]


@pytest.mark.asyncio
async def test_stream_always_ends_with_done(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_with_citations
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={"message": "Anything"},
        headers={"X-User-Id": "sarah_chen"},
    )

    events = _parse_sse_events(resp.text)
    last_event = events[-1]
    assert last_event["event"] == "done"
    assert json.loads(last_event["data"]) == {}


@pytest.mark.asyncio
async def test_conversation_id_accepted(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_with_citations
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={"message": "Follow up question", "conversation_id": "conv-12345"},
        headers={"X-User-Id": "sarah_chen"},
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_different_users_get_same_stream_format(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_with_citations
    override_dependencies[get_redactor] = RegexRedactor

    for user_id in ["sarah_chen", "alex_kim", "james_wright", "priya_sharma"]:
        resp = await test_client.post(
            "/api/chat/stream",
            json={"message": "Test message"},
            headers={"X-User-Id": user_id},
        )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        event_types = [e.get("event") for e in events]
        assert "done" in event_types


@pytest.mark.asyncio
async def test_missing_message_returns_422(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_with_citations
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={},
        headers={"X-User-Id": "sarah_chen"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_request_id_header_in_response(
    test_client: AsyncClient, override_dependencies: dict[object, object]
) -> None:
    override_dependencies[get_agent] = _mock_agent_with_citations
    override_dependencies[get_redactor] = RegexRedactor

    resp = await test_client.post(
        "/api/chat/stream",
        json={"message": "Test"},
        headers={"X-User-Id": "sarah_chen", "X-Request-Id": "trace-abc-123"},
    )

    assert resp.status_code == 200
    assert resp.headers.get("X-Request-Id") == "trace-abc-123"
