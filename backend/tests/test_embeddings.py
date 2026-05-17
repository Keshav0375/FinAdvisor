from __future__ import annotations

import os

import pytest

from src.retrieval.embeddings import DIMENSIONS, VoyageEmbeddings

VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")

requires_voyage = pytest.mark.skipif(
    not VOYAGE_API_KEY,
    reason="VOYAGE_API_KEY not set",
)


@requires_voyage
@pytest.mark.asyncio
async def test_embed_single_sentence_returns_1024_dims() -> None:
    client = VoyageEmbeddings(api_key=VOYAGE_API_KEY)
    result = await client.embed_query("The fund returned 8.5% in Q3 2024.")
    assert len(result) == DIMENSIONS
    assert all(isinstance(v, float) for v in result)


@requires_voyage
@pytest.mark.asyncio
async def test_embed_batch_returns_correct_count() -> None:
    client = VoyageEmbeddings(api_key=VOYAGE_API_KEY)
    texts = [
        "Conservative fixed income portfolio allocation.",
        "EU MiFID II suitability requirements.",
        "High-net-worth client risk assessment.",
    ]
    results = await client.embed_texts(texts)
    assert len(results) == 3
    assert all(len(vec) == DIMENSIONS for vec in results)
