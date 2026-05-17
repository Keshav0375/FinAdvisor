from __future__ import annotations

import asyncio
from typing import Any

import structlog
import voyageai

log = structlog.get_logger()

DEFAULT_MODEL = "voyage-3"
DIMENSIONS = 1024
MAX_BATCH_SIZE = 128
MAX_RETRIES = 3
BASE_DELAY = 1.0
RATE_LIMIT_RPS = 3


class VoyageEmbeddings:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._client: Any = voyageai.Client(api_key=api_key)  # type: ignore[attr-defined]
        self._model = model
        self._semaphore = asyncio.Semaphore(RATE_LIMIT_RPS)
        self._min_interval = 1.0 / RATE_LIMIT_RPS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[i : i + MAX_BATCH_SIZE]
            embeddings = await self._embed_batch_with_retry(batch)
            all_embeddings.extend(embeddings)
        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed_texts([text])
        return results[0]

    async def _embed_batch_with_retry(self, texts: list[str]) -> list[list[float]]:
        for attempt in range(MAX_RETRIES):
            try:
                async with self._semaphore:
                    result = await asyncio.to_thread(
                        self._client.embed,
                        texts,
                        model=self._model,
                        input_type="document",
                    )
                    await asyncio.sleep(self._min_interval)
                log.info(
                    "embedding_batch_complete",
                    batch_size=len(texts),
                    model=self._model,
                )
                return result.embeddings  # type: ignore[no-any-return]
            except Exception as exc:
                if attempt == MAX_RETRIES - 1:
                    log.error(
                        "embedding_batch_failed",
                        batch_size=len(texts),
                        attempts=MAX_RETRIES,
                        error=str(exc),
                    )
                    raise
                delay = BASE_DELAY * (2**attempt)
                log.warning(
                    "embedding_batch_retry",
                    attempt=attempt + 1,
                    delay=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")
