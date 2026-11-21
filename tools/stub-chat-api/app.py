"""Stub chat-api for gateway tests (E2). Test tooling only: never deployed, not chat-api (E4)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from config import Settings
from tokens import TokenError, build_unsigned_jwt, decode_unverified, digest_header

# Header names whose value is a credential: echoed as {sha256, length} only.
REDACTED_HEADERS = frozenset({"authorization", "x-serverless-authorization", "proxy-authorization"})
SERVERLESS_AUTH_HEADER = "x-serverless-authorization"
IDENTITY_PATH = "/metadata-mock/computeMetadata/v1/instance/service-accounts/default/identity"
MAX_AUDIENCE_LENGTH = 2048
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def echo_headers(request: Request) -> dict[str, Any]:
    """Received headers by lowercase name; credential headers become {sha256, length}."""
    headers: dict[str, Any] = {}
    for name, value in request.headers.items():
        if name in REDACTED_HEADERS:
            headers[name] = digest_header(value)
        else:
            headers[name] = value
    return headers


def serverless_auth_claims(request: Request) -> dict[str, Any] | None:
    """Unverified aud/email/exp of X-Serverless-Authorization; an error object if malformed."""
    value = request.headers.get(SERVERLESS_AUTH_HEADER)
    if value is None:
        return None
    try:
        return decode_unverified(value)
    except TokenError as exc:
        return {"error": str(exc)}


def sse_frame(event_name: str, seq: int, emit_ts_ns: int) -> str:
    data = json.dumps({"seq": seq, "emit_ts_ns": emit_ts_ns}, separators=(",", ":"))
    return f"id: {seq}\nevent: {event_name}\ndata: {data}\n\n"


async def sse_stream(event_name: str, events: int, interval_s: float) -> AsyncIterator[str]:
    """Yield `events` frames, one flush each. Emit timestamps are strictly increasing."""
    last_ts = 0
    for seq in range(1, events + 1):
        last_ts = max(time.time_ns(), last_ts + 1)
        yield sse_frame(event_name, seq, last_ts)
        if seq < events and interval_s > 0:
            await asyncio.sleep(interval_s)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    app = FastAPI(title="stub-chat-api", docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/echo", methods=["GET", "POST"])
    async def echo(request: Request) -> dict[str, Any]:
        return {
            "method": request.method,
            "path": request.url.path,
            "headers": echo_headers(request),
            "serverless_auth": serverless_auth_claims(request),
        }

    @app.get("/sse")
    async def sse(
        events: Annotated[int, Query(ge=1)] = 10,
        interval_ms: Annotated[int, Query(ge=0)] = 100,
    ) -> StreamingResponse:
        if events > cfg.sse_max_events:
            raise HTTPException(422, f"events must be <= {cfg.sse_max_events}")
        if interval_ms > cfg.sse_max_interval_ms:
            raise HTTPException(422, f"interval_ms must be <= {cfg.sse_max_interval_ms}")
        if events * interval_ms > cfg.sse_max_duration_s * 1000:
            raise HTTPException(
                422, f"events * interval_ms must be <= {cfg.sse_max_duration_s * 1000} ms"
            )
        return StreamingResponse(
            sse_stream(cfg.sse_event_name, events, interval_ms / 1000),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @app.get(IDENTITY_PATH)
    async def identity(
        audience: Annotated[str, Query(min_length=1, max_length=MAX_AUDIENCE_LENGTH)],
    ) -> PlainTextResponse:
        token = build_unsigned_jwt(
            audience, cfg.metadata_email, int(time.time()), cfg.metadata_token_ttl_s
        )
        return PlainTextResponse(token, headers={"Metadata-Flavor": "Google"})

    return app


app = create_app()
