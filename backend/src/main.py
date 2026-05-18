from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import anthropic
import structlog
from fastapi import FastAPI, Request, Response

from src.config import Settings
from src.db.session import build_engine
from src.pii import create_redactor
from src.retrieval.embeddings import VoyageEmbeddings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings

    app.state.db = build_engine(settings)
    log.info("db_pool_initialized", url=settings.database_url.split("@")[-1])

    app.state.embeddings = VoyageEmbeddings(api_key=settings.voyage_api_key)
    log.info("embeddings_initialized", model="voyage-3")

    app.state.redactor = create_redactor(settings)
    log.info("pii_redactor_initialized", mode=settings.pii_mode)

    app.state.llm_client = anthropic.AsyncAnthropic(
        api_key=settings.litellm_master_key,
        base_url=f"{settings.kong_url}/v1",
    )
    log.info("llm_client_initialized", base_url=f"{settings.kong_url}/v1")

    if settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            from langfuse import Langfuse

            app.state.langfuse = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            log.info("langfuse_initialized", host=settings.langfuse_host)
        except ImportError:
            log.warning("langfuse_not_installed")
            app.state.langfuse = None
    else:
        app.state.langfuse = None
        log.info("langfuse_skipped", reason="no keys configured")

    log.info("app_startup_complete")
    yield

    if hasattr(app.state, "langfuse") and app.state.langfuse is not None:
        app.state.langfuse.shutdown()
    log.info("app_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="FinAdvisor API", lifespan=lifespan)

    app.middleware("http")(request_logging_middleware)

    from src.api.router import api_router

    app.include_router(api_router, prefix="/api")

    return app


async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Coroutine[Any, Any, Response]],
) -> Response:
    start = time.perf_counter()
    user_id = request.headers.get("X-User-Id", "anonymous")

    response: Response = await call_next(request)

    duration_ms = (time.perf_counter() - start) * 1000
    log.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 1),
        user_id=user_id,
    )
    return response


app = create_app()
