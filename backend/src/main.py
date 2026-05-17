from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("app_startup")
    yield
    log.info("app_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="FinAdvisor API", lifespan=lifespan)

    app.middleware("http")(request_logging_middleware)

    from src.api.health import router as health_router

    app.include_router(health_router, prefix="/api")

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
