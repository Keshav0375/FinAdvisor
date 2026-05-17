from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from src.auth.jwt import get_current_user
from src.auth.models import UserClaims

app = FastAPI()


@app.get("/me")
async def me(user: UserClaims = Depends(get_current_user)) -> dict[str, object]:
    return user.model_dump()


@pytest.mark.asyncio
async def test_valid_user_returns_claims() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/me", headers={"X-User-Id": "sarah_chen"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sub"] == "sarah_chen"
    assert data["tier_level"] == 3
    assert data["jurisdictions"] == ["US"]


@pytest.mark.asyncio
async def test_unknown_user_returns_401() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/me", headers={"X-User-Id": "nobody"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_header_returns_401() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/me")
    assert resp.status_code == 401
