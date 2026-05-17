from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_valid_user_returns_claims() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/me", headers={"X-User-Id": "sarah_chen"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sub"] == "sarah_chen"
    assert data["tier_level"] == 3
    assert data["jurisdictions"] == ["US"]


@pytest.mark.asyncio
async def test_unknown_user_returns_401() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/me", headers={"X-User-Id": "nobody"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_header_returns_401() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_request_logging_includes_user_id(capsys: pytest.CaptureFixture[str]) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/me", headers={"X-User-Id": "alex_kim"})
