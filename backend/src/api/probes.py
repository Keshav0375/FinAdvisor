from __future__ import annotations

import asyncio
from datetime import date
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


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
    problems = check_models(settings, today=date.today())
    for problem in problems:
        log.warning(
            "readyz_model_problem",
            key=problem.key,
            model=problem.model,
            reason=problem.reason,
            fatal=problem.fatal,
        )
    return "fail" if any(problem.fatal for problem in problems) else "ok"


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    checks: dict[str, CheckResult] = {
        "db": await _check_db(request, settings),
        "models": _check_models(settings),
    }
    ready = all(result == "ok" for result in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
