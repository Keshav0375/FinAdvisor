from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.tools.search_kb import (
    SearchKBInput,
    SearchKBOutput,
    search_firm_kb,
)
from src.db.rls import set_rls_context
from src.retrieval.vector_store import VectorStore

from .conftest import SARAH_CHEN, requires_db


@pytest.mark.asyncio
async def test_search_kb_with_mocked_deps() -> None:
    mock_embeddings = AsyncMock()
    mock_embeddings.embed_query.return_value = [0.01] * 1024

    mock_vector_store = AsyncMock(spec=VectorStore)
    mock_vector_store.similarity_search.return_value = [
        {
            "chunk_id": "abc-123",
            "content": "Bond ETF product details",
            "source_title": "Vanguard Bond ETF Factsheet",
            "regulatory_ref": "FINRA Rule 2111",
            "last_reviewed_at": "2025-03-15",
            "similarity_score": 0.92,
        },
        {
            "chunk_id": "def-456",
            "content": "Fixed income suitability guidance",
            "source_title": "Suitability Policy",
            "regulatory_ref": None,
            "last_reviewed_at": "2024-01-10",
            "similarity_score": 0.85,
        },
    ]

    input_data = SearchKBInput(query="bond ETF suitability", top_k=5)
    result = await search_firm_kb(input_data, mock_vector_store, mock_embeddings)

    assert isinstance(result, SearchKBOutput)
    assert result.total_found == 2
    assert len(result.results) == 2
    assert result.results[0].chunk_id == "abc-123"
    assert result.results[0].source_title == "Vanguard Bond ETF Factsheet"
    assert result.results[0].similarity_score == 0.92
    assert result.results[1].regulatory_ref is None

    mock_embeddings.embed_query.assert_awaited_once_with("bond ETF suitability")
    mock_vector_store.similarity_search.assert_awaited_once_with(
        query_embedding=[0.01] * 1024, top_k=5
    )


@pytest.mark.asyncio
async def test_search_kb_empty_results() -> None:
    mock_embeddings = AsyncMock()
    mock_embeddings.embed_query.return_value = [0.01] * 1024

    mock_vector_store = AsyncMock(spec=VectorStore)
    mock_vector_store.similarity_search.return_value = []

    input_data = SearchKBInput(query="nonexistent topic")
    result = await search_firm_kb(input_data, mock_vector_store, mock_embeddings)

    assert result.total_found == 0
    assert result.results == []


@requires_db
@pytest.mark.asyncio
async def test_search_kb_integration_with_rls(live_session: AsyncSession) -> None:
    await set_rls_context(live_session, SARAH_CHEN)
    vector_store = VectorStore(live_session)

    query_vec = [0.01] * 1024
    with patch("src.agent.tools.search_kb.VoyageEmbeddings", autospec=True) as mock_emb_cls:
        mock_instance = mock_emb_cls.return_value
        mock_instance.embed_query = AsyncMock(return_value=query_vec)

        input_data = SearchKBInput(query="compliance rules", top_k=5)
        result = await search_firm_kb(input_data, vector_store, mock_instance)

    assert result.total_found == 5
    for chunk in result.results:
        assert chunk.chunk_id
        assert chunk.content
        assert chunk.source_title
        assert chunk.similarity_score > 0


@requires_db
@pytest.mark.asyncio
async def test_vector_store_respects_rls(live_session: AsyncSession) -> None:
    await set_rls_context(live_session, SARAH_CHEN)
    vector_store = VectorStore(live_session)

    results = await vector_store.similarity_search(query_embedding=[0.01] * 1024, top_k=100)

    assert len(results) > 0
    assert len(results) == 43
