from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.orchestrator import FinAdvisorAgent
from src.agent.system_prompt import SYSTEM_PROMPT_VERSION, get_system_prompt
from src.agent.types import StreamEvent

from .conftest import SARAH_CHEN


def _mock_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _mock_tool_use_block(
    name: str, input_data: dict[str, object], tool_id: str = "toolu_123"
) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input_data
    block.id = tool_id
    return block


def _mock_response(
    stop_reason: str,
    content: list[MagicMock],
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> MagicMock:
    response = MagicMock()
    response.stop_reason = stop_reason
    response.content = content
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response.model_dump.return_value = {"stop_reason": stop_reason}
    return response


def _make_agent(client: AsyncMock) -> tuple[FinAdvisorAgent, AsyncMock]:
    registry = MagicMock()
    registry.to_anthropic_schema.return_value = [{"name": "search_firm_kb"}]
    registry.execute = AsyncMock(return_value='{"results": [], "total_found": 0}')

    agent = FinAdvisorAgent(
        client=client,
        tool_registry=registry,
    )
    return agent, registry


@pytest.mark.asyncio
async def test_single_turn_text_response() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_mock_response(
            "end_turn",
            [_mock_text_block("Based on firm documentation [1], bonds are suitable.")],
        )
    )

    agent, _registry = _make_agent(client)
    events: list[StreamEvent] = []
    async for event in agent.run("Tell me about bonds", SARAH_CHEN):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == "text"
    assert "bonds are suitable" in (events[0].content or "")


@pytest.mark.asyncio
async def test_tool_use_then_text_response() -> None:
    client = AsyncMock()

    tool_response = _mock_response(
        "tool_use",
        [_mock_tool_use_block("search_firm_kb", {"query": "bond funds", "top_k": 5})],
    )
    text_response = _mock_response(
        "end_turn",
        [_mock_text_block("Bond funds are available [1].")],
    )

    client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

    agent, registry = _make_agent(client)
    events: list[StreamEvent] = []
    async for event in agent.run("bond funds info", SARAH_CHEN):
        events.append(event)

    assert len(events) == 3
    assert events[0].type == "tool_call"
    assert events[0].tool_call is not None
    assert events[0].tool_call.tool_name == "search_firm_kb"
    assert events[1].type == "tool_result"
    assert events[2].type == "text"
    assert "Bond funds" in (events[2].content or "")

    registry.execute.assert_called_once()


@pytest.mark.asyncio
async def test_max_iterations_yields_error() -> None:
    client = AsyncMock()

    tool_response = _mock_response(
        "tool_use",
        [_mock_tool_use_block("search_firm_kb", {"query": "loop"})],
    )
    client.messages.create = AsyncMock(return_value=tool_response)

    agent, _registry = _make_agent(client)
    events: list[StreamEvent] = []
    async for event in agent.run("loop query", SARAH_CHEN, max_iterations=2):
        events.append(event)

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 1
    assert "Max reasoning iterations" in (error_events[0].content or "")


@pytest.mark.asyncio
async def test_tool_execution_error_handled() -> None:
    client = AsyncMock()

    tool_response = _mock_response(
        "tool_use",
        [_mock_tool_use_block("search_firm_kb", {"query": "fail"})],
    )
    text_response = _mock_response(
        "end_turn",
        [_mock_text_block("Sorry, I could not retrieve results.")],
    )
    client.messages.create = AsyncMock(side_effect=[tool_response, text_response])

    agent, registry = _make_agent(client)
    registry.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))

    events: list[StreamEvent] = []
    async for event in agent.run("failing query", SARAH_CHEN):
        events.append(event)

    tool_result_events = [e for e in events if e.type == "tool_result"]
    assert len(tool_result_events) == 1
    assert "DB connection lost" in (tool_result_events[0].content or "")

    text_events = [e for e in events if e.type == "text"]
    assert len(text_events) == 1


@pytest.mark.asyncio
async def test_langfuse_trace_integration() -> None:
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_mock_response("end_turn", [_mock_text_block("Answer.")])
    )

    mock_langfuse = MagicMock()
    mock_trace = MagicMock()
    mock_langfuse.trace.return_value = mock_trace
    mock_generation = MagicMock()
    mock_trace.generation.return_value = mock_generation

    registry = MagicMock()
    registry.to_anthropic_schema.return_value = []

    agent = FinAdvisorAgent(
        client=client,
        tool_registry=registry,
        langfuse=mock_langfuse,
    )

    events: list[StreamEvent] = []
    async for event in agent.run("test query", SARAH_CHEN):
        events.append(event)

    mock_langfuse.trace.assert_called_once()
    mock_trace.generation.assert_called_once()
    mock_generation.end.assert_called_once()
    mock_trace.update.assert_called_once()


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_response() -> None:
    client = AsyncMock()

    multi_tool_response = _mock_response(
        "tool_use",
        [
            _mock_tool_use_block("search_firm_kb", {"query": "bonds"}, "toolu_1"),
            _mock_tool_use_block(
                "lookup_suitability_rule",
                {"product_category": "fixed_income", "client_risk_profile": "conservative"},
                "toolu_2",
            ),
        ],
    )
    text_response = _mock_response(
        "end_turn",
        [_mock_text_block("Combined answer.")],
    )
    client.messages.create = AsyncMock(side_effect=[multi_tool_response, text_response])

    agent, registry = _make_agent(client)
    events: list[StreamEvent] = []
    async for event in agent.run("bonds suitability", SARAH_CHEN):
        events.append(event)

    tool_calls = [e for e in events if e.type == "tool_call"]
    assert len(tool_calls) == 2
    assert tool_calls[0].tool_call is not None
    assert tool_calls[0].tool_call.tool_name == "search_firm_kb"
    assert tool_calls[1].tool_call is not None
    assert tool_calls[1].tool_call.tool_name == "lookup_suitability_rule"

    assert registry.execute.call_count == 2


def test_system_prompt_content() -> None:
    prompt = get_system_prompt()
    assert "FinAdvisor" in prompt
    assert "CRITICAL RULES" in prompt
    assert "[N]" in prompt
    assert "escalate_to_compliance" in prompt
    assert "search_firm_kb" in prompt


def test_system_prompt_version() -> None:
    assert SYSTEM_PROMPT_VERSION == "1.0.0"
