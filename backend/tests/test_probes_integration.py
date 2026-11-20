from __future__ import annotations

import socket
import time
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import Settings
from src.db.session import build_engine
from src.main import create_app
from tests.conftest import TEST_DATABASE_URL, requires_db

CLOSED_PORT_BUDGET_S = 5.0


def _closed_port() -> int:
    """A local port with nothing listening: bind, read the number, close."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _client_for_db(monkeypatch: pytest.MonkeyPatch, host: str, port: int) -> AsyncClient:
    """App (no lifespan) whose engine is built from Settings pointing at host:port."""
    parts = urlsplit(TEST_DATABASE_URL)
    monkeypatch.setenv("DB_HOST", host)
    monkeypatch.setenv("DB_PORT", str(port))
    monkeypatch.setenv("DB_NAME", (parts.path or "/postgres").lstrip("/"))
    monkeypatch.setenv("DB_USER", parts.username or "postgres")
    monkeypatch.setenv("DB_PASSWORD", parts.password or "postgres-test-pass")
    settings = Settings()  # type: ignore[call-arg]
    app = create_app()
    app.state.settings = settings
    app.state.db = build_engine(settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@requires_db
async def test_readyz_200_against_real_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    parts = urlsplit(TEST_DATABASE_URL)
    async with _client_for_db(
        monkeypatch, parts.hostname or "localhost", parts.port or 5432
    ) as client:
        response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"db": "ok", "models": "ok"}}


@requires_db
async def test_readyz_503_within_5s_on_closed_port(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client_for_db(monkeypatch, "127.0.0.1", _closed_port()) as client:
        started = time.monotonic()
        response = await client.get("/readyz")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["checks"]["db"] == "fail"
    assert elapsed < CLOSED_PORT_BUDGET_S
