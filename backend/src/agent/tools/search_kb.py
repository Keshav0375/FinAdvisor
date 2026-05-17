from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from src.retrieval.embeddings import VoyageEmbeddings
from src.retrieval.vector_store import VectorStore

log = structlog.get_logger()


class SearchKBInput(BaseModel):
    query: str = Field(description="Natural language search query")
    top_k: int = Field(default=5, le=10, description="Number of results to return")


class ChunkResult(BaseModel):
    chunk_id: str
    content: str
    source_title: str
    regulatory_ref: str | None
    last_reviewed_at: str
    similarity_score: float


class SearchKBOutput(BaseModel):
    results: list[ChunkResult]
    total_found: int


SEARCH_KB_TOOL = {
    "name": "search_firm_kb",
    "description": (
        "Search the firm's knowledge base for relevant product information, "
        "compliance rules, and policy documents. Results are filtered by the "
        "advisor's tier and jurisdiction automatically."
    ),
    "input_schema": SearchKBInput.model_json_schema(),
}


async def search_firm_kb(
    input_data: SearchKBInput,
    vector_store: VectorStore,
    embeddings: VoyageEmbeddings,
) -> SearchKBOutput:
    log.info("search_kb_start", query=input_data.query, top_k=input_data.top_k)

    query_embedding = await embeddings.embed_query(input_data.query)

    raw_results = await vector_store.similarity_search(
        query_embedding=query_embedding,
        top_k=input_data.top_k,
    )

    results = [ChunkResult(**r) for r in raw_results]

    log.info("search_kb_complete", results_count=len(results))
    return SearchKBOutput(results=results, total_found=len(results))
