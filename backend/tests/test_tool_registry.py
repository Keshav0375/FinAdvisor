from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.agent.tools import ToolRegistry
from src.retrieval.embeddings import VoyageEmbeddings

from .conftest import ALEX_KIM, SARAH_CHEN


def _make_registry() -> ToolRegistry:
    session = AsyncMock()
    embeddings = MagicMock(spec=VoyageEmbeddings)
    return ToolRegistry(session=session, embeddings=embeddings)


@pytest.mark.asyncio
async def test_to_anthropic_schema_returns_all_tools() -> None:
    registry = _make_registry()
    schemas = registry.to_anthropic_schema()

    assert len(schemas) == 4
    names = {s["name"] for s in schemas}
    assert names == {
        "search_firm_kb",
        "lookup_suitability_rule",
        "lookup_product_factsheet",
        "escalate_to_compliance",
    }


@pytest.mark.asyncio
async def test_anthropic_schema_has_required_fields() -> None:
    registry = _make_registry()
    for schema in registry.to_anthropic_schema():
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema
        assert isinstance(schema["input_schema"], dict)
        assert "properties" in schema["input_schema"]


@pytest.mark.asyncio
async def test_tool_names_property() -> None:
    registry = _make_registry()
    assert set(registry.tool_names) == {
        "search_firm_kb",
        "lookup_suitability_rule",
        "lookup_product_factsheet",
        "escalate_to_compliance",
    }


@pytest.mark.asyncio
async def test_execute_escalate_dispatches_correctly() -> None:
    registry = _make_registry()
    result_json = await registry.execute(
        "escalate_to_compliance",
        {
            "reason": "License mismatch",
            "product_class": "derivatives",
            "advisor_licenses": ["Series-7"],
        },
        SARAH_CHEN,
    )

    result = json.loads(result_json)
    assert result["advisor_id"] == "sarah_chen"
    assert result["reason"] == "License mismatch"
    assert result["product_class"] == "derivatives"
    assert result["status"] == "pending_review"


@pytest.mark.asyncio
async def test_execute_unknown_tool_raises() -> None:
    registry = _make_registry()
    with pytest.raises(ValueError, match="Unknown tool: nonexistent"):
        await registry.execute("nonexistent", {}, SARAH_CHEN)


@pytest.mark.asyncio
async def test_execute_search_kb_dispatches() -> None:
    registry = _make_registry()

    mock_embedding = [0.1] * 1024
    with (
        patch.object(
            registry._embeddings, "embed_query", new_callable=AsyncMock, return_value=mock_embedding
        ),
        patch.object(
            registry._vector_store,
            "similarity_search",
            new_callable=AsyncMock,
            return_value=[
                {
                    "chunk_id": "abc-123",
                    "content": "Test content",
                    "source_title": "Test Doc",
                    "regulatory_ref": "REG-1",
                    "last_reviewed_at": "2025-01-15",
                    "similarity_score": 0.95,
                }
            ],
        ),
    ):
        result_json = await registry.execute(
            "search_firm_kb",
            {"query": "bond funds", "top_k": 3},
            SARAH_CHEN,
        )

    result = json.loads(result_json)
    assert result["total_found"] == 1
    assert result["results"][0]["source_title"] == "Test Doc"


@pytest.mark.asyncio
async def test_execute_returns_json_string() -> None:
    registry = _make_registry()
    result = await registry.execute(
        "escalate_to_compliance",
        {
            "reason": "Test",
            "product_class": "equity",
            "advisor_licenses": ["MiFID-II"],
        },
        ALEX_KIM,
    )

    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["advisor_id"] == "alex_kim"


@pytest.mark.asyncio
async def test_execute_validates_input() -> None:
    registry = _make_registry()
    with pytest.raises(ValidationError):
        await registry.execute(
            "escalate_to_compliance",
            {"invalid_field": "value"},
            SARAH_CHEN,
        )
