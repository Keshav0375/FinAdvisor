"""
Live fallback chain verification script.

Run with Docker Compose stack up:
    docker compose up -d postgres litellm kong
    python scripts/test_fallback_live.py

Tests:
1. Primary route (Claude) succeeds through Kong → LiteLLM
2. Health check on LiteLLM
3. Rate limit header presence from Kong
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

KONG_URL = "http://localhost:8001"
LITELLM_URL = "http://localhost:4000"


async def check_litellm_health() -> bool:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{LITELLM_URL}/health", timeout=5.0)
            log.info("litellm_health", status=resp.status_code, body=resp.text[:200])
            return resp.status_code == 200
        except httpx.ConnectError:
            log.error("litellm_unreachable", url=LITELLM_URL)
            return False


async def test_primary_route() -> bool:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{KONG_URL}/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 50,
                    "messages": [{"role": "user", "content": "Say hello in one word."}],
                },
                headers={
                    "X-User-Id": "sarah_chen",
                    "Content-Type": "application/json",
                    "Authorization": "Bearer sk-litellm-local",
                },
                timeout=30.0,
            )
            log.info(
                "primary_route",
                status=resp.status_code,
                rate_limit_remaining=resp.headers.get("X-RateLimit-Remaining-Minute"),
                body=resp.text[:300],
            )
            return resp.status_code == 200
        except httpx.ConnectError:
            log.error("kong_unreachable", url=KONG_URL)
            return False


async def test_rate_limit_headers() -> bool:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{KONG_URL}/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
                headers={
                    "X-User-Id": "test_rate_limit",
                    "Content-Type": "application/json",
                    "Authorization": "Bearer sk-litellm-local",
                },
                timeout=30.0,
            )
            has_rate_headers = any("ratelimit" in k.lower() for k in resp.headers)
            log.info(
                "rate_limit_check",
                status=resp.status_code,
                has_rate_headers=has_rate_headers,
                headers={k: v for k, v in resp.headers.items() if "ratelimit" in k.lower()},
            )
            return has_rate_headers
        except httpx.ConnectError:
            log.error("kong_unreachable_rate_check")
            return False


async def main() -> None:
    results: dict[str, Any] = {}

    log.info("fallback_verification_start")

    results["litellm_health"] = await check_litellm_health()
    results["primary_route"] = await test_primary_route()
    results["rate_limit_headers"] = await test_rate_limit_headers()

    log.info("fallback_verification_results", **results)

    passed = all(results.values())
    if passed:
        log.info("all_checks_passed")
    else:
        failed = [k for k, v in results.items() if not v]
        log.error("checks_failed", failed=failed)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
