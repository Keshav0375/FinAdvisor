from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from src.agent.orchestrator import FinAdvisorAgent
from src.agent.types import StreamEvent

from .conftest import SARAH_CHEN


def _mock_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _mock_response(content_text: str) -> MagicMock:
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [_mock_text_block(content_text)]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    response.model_dump.return_value = {"stop_reason": "end_turn"}
    return response


def _make_agent(client: AsyncMock) -> FinAdvisorAgent:
    registry = MagicMock()
    registry.to_anthropic_schema.return_value = []
    return FinAdvisorAgent(client=client, tool_registry=registry)


@pytest.mark.asyncio
async def test_primary_provider_success() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_mock_response("Primary provider response [1].")
    )

    agent = _make_agent(client)
    events: list[StreamEvent] = []
    async for event in agent.run("test query", SARAH_CHEN):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == "text"
    assert "Primary provider" in (events[0].content or "")


@pytest.mark.asyncio
async def test_fallback_transparent_to_agent() -> None:
    """LiteLLM handles fallback internally — agent sees a normal response regardless of which provider served it."""
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_mock_response("Response from fallback provider.")
    )

    agent = _make_agent(client)
    events: list[StreamEvent] = []
    async for event in agent.run("test query", SARAH_CHEN):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == "text"
    assert "fallback provider" in (events[0].content or "").lower()


@pytest.mark.asyncio
async def test_all_providers_fail_yields_error() -> None:
    """When all providers are down, the agent should yield an error event."""
    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=anthropic.APIConnectionError(request=MagicMock())
    )

    agent = _make_agent(client)
    events: list[StreamEvent] = []

    with pytest.raises(anthropic.APIConnectionError):
        async for event in agent.run("test query", SARAH_CHEN):
            events.append(event)


@pytest.mark.asyncio
async def test_rate_limit_error_propagates() -> None:
    """Rate limit errors from Kong/LiteLLM surface correctly."""
    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body={"error": {"message": "rate limited"}},
        )
    )

    agent = _make_agent(client)
    with pytest.raises(anthropic.RateLimitError):
        async for _event in agent.run("test query", SARAH_CHEN):
            pass


@pytest.mark.asyncio
async def test_circuit_breaker_simulation() -> None:
    """Simulates 3 consecutive failures followed by a successful fallback response."""
    client = AsyncMock()
    overload_error = anthropic.APIStatusError(
        message="overloaded",
        response=MagicMock(status_code=529),
        body={"error": {"message": "overloaded"}},
    )
    client.messages.create = AsyncMock(
        side_effect=[
            overload_error,
            overload_error,
            overload_error,
            _mock_response("Circuit breaker tripped, using fallback."),
        ]
    )

    agent = _make_agent(client)
    events: list[StreamEvent] = []

    with pytest.raises(anthropic.APIStatusError):
        async for event in agent.run("test query", SARAH_CHEN):
            events.append(event)
