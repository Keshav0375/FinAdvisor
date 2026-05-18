from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import anthropic
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from src.config import Settings
from src.db.session import build_engine
from src.errors import AuthorizationError, FinAdvisorError, RetrievalError, ToolExecutionError
from src.observability.logging import RequestContextMiddleware, configure_logging
from src.pii import create_redactor
from src.retrieval.embeddings import VoyageEmbeddings

log = structlog.get_logger()

_ERROR_STATUS_MAP: dict[type[FinAdvisorError], int] = {
    AuthorizationError: 403,
    RetrievalError: 502,
    ToolExecutionError: 500,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(json_output=True)

    settings = Settings()
    app.state.settings = settings

    app.state.db = build_engine(settings)
    log.info("db_pool_initialized", url=settings.database_url.split("@")[-1])

    app.state.embeddings = VoyageEmbeddings(api_key=settings.voyage_api_key)
    log.info("embeddings_initialized", model="voyage-3")

    app.state.redactor = create_redactor(settings)
    log.info("pii_redactor_initialized", mode=settings.pii_mode)

    llm_base = settings.llm_base_url or f"{settings.kong_url}/v1"
    llm_key = settings.anthropic_api_key if settings.llm_base_url else settings.litellm_master_key
    app.state.llm_client = anthropic.AsyncAnthropic(
        api_key=llm_key,
        base_url=llm_base,
    )
    log.info("llm_client_initialized", base_url=llm_base)

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

    app.add_middleware(RequestContextMiddleware)
    app.middleware("http")(request_logging_middleware)

    app.add_exception_handler(FinAdvisorError, finadvisor_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    from src.api.router import api_router

    app.include_router(api_router, prefix="/api")

    return app


async def finadvisor_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, FinAdvisorError)
    status = _ERROR_STATUS_MAP.get(type(exc), 500)

    log.error(
        "finadvisor_error",
        error_type=type(exc).__name__,
        detail=str(exc),
        path=request.url.path,
    )

    return JSONResponse(
        status_code=status,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "unhandled_error",
        error_type=type(exc).__name__,
        detail=str(exc),
        path=request.url.path,
        exc_info=True,
    )

    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "detail": "An unexpected error occurred."},
    )


async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Coroutine[Any, Any, Response]],
) -> Response:
    start = time.perf_counter()
    user_id = request.headers.get("X-User-Id", "anonymous")

    try:
        response: Response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        log.error(
            "unhandled_error",
            method=request.method,
            path=request.url.path,
            duration_ms=round(duration_ms, 1),
            user_id=user_id,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "InternalServerError", "detail": "An unexpected error occurred."},
        )

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
