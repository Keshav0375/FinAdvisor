from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()


class VectorStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        query_vec = str(query_embedding)
        result = await self._session.execute(
            text("""
                SELECT id, content, source_title, regulatory_ref,
                       last_reviewed_at,
                       1 - (embedding <=> cast(:query_vec as vector)) AS similarity_score
                FROM chunks
                ORDER BY embedding <=> cast(:query_vec as vector)
                LIMIT :top_k
            """),
            {"query_vec": query_vec, "top_k": top_k},
        )
        rows = result.fetchall()
        log.info("vector_search_complete", results=len(rows), top_k=top_k)
        return [
            {
                "chunk_id": str(row.id),
                "content": row.content,
                "source_title": row.source_title,
                "regulatory_ref": row.regulatory_ref,
                "last_reviewed_at": str(row.last_reviewed_at),
                "similarity_score": round(float(row.similarity_score), 4),
            }
            for row in rows
        ]
