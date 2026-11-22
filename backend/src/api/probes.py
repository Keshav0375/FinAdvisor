from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.config import Settings
from src.model_lifecycle import check_models

log = structlog.get_logger()

router = APIRouter()

CheckResult = Literal["ok", "fail"]
Checks = dict[str, CheckResult]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _utc_today() -> date:
    """UTC date, as the E0-T4 CLI uses, so CI and /readyz agree near midnight."""
    return datetime.now(UTC).date()


class ReadinessCache:
    """Last readiness result, kept for a TTL; concurrent callers share one in-flight probe.

    /readyz is unauthenticated and each probe takes a pool connection, so a flood must not
    translate into a flood of probes. Lives on app.state (one per app, no module state).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._result: Checks | None = None
        self._expires_at = 0.0

    def _fresh(self) -> Checks | None:
        if self._result is not None and self._clock() < self._expires_at:
            return self._result
        return None

    async def get(self, ttl_s: float, probe: Callable[[], Awaitable[Checks]]) -> Checks:
        cached = self._fresh()
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._fresh()
            if cached is not None:
                return cached
            result = await probe()
            self._result = result
            self._expires_at = self._clock() + ttl_s
            return result


async def _check_db(request: Request, settings: Settings) -> CheckResult:
    try:
        async with asyncio.timeout(settings.readiness_timeout_s):
            async with request.app.state.db() as session:
                await session.execute(text("SELECT 1"))
    except Exception as exc:
        # Type only: the message can carry the host or the DSN.
        log.warning("readyz_db_failed", error_type=type(exc).__name__)
        return "fail"
    return "ok"


def _check_models(settings: Settings) -> CheckResult:
    problems = check_models(settings, today=_utc_today())
    for problem in problems:
        log.warning(
            "readyz_model_problem",
            key=problem.key,
            model=problem.model,
            reason=problem.reason,
            fatal=problem.fatal,
        )
    return "fail" if any(problem.fatal for problem in problems) else "ok"


def _readiness_cache(request: Request) -> ReadinessCache:
    # Created on first use so the lifespan stays untouched; no await between check and set.
    cache: ReadinessCache | None = getattr(request.app.state, "readiness_cache", None)
    if cache is None:
        cache = ReadinessCache()
        request.app.state.readiness_cache = cache
    return cache


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings

    async def probe() -> Checks:
        return {
            "db": await _check_db(request, settings),
            "models": _check_models(settings),
        }

    checks = await _readiness_cache(request).get(settings.readiness_cache_ttl_s, probe)
    ready = all(result == "ok" for result in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
