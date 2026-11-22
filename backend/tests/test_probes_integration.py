from __future__ import annotations

import socket
import time

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import Settings
from src.db.session import build_engine
from src.main import create_app
from tests.conftest import TEST_DATABASE_URL

CLOSED_PORT_BUDGET_S = 5.0


def _closed_port() -> int:
    """A local port with nothing listening: bind, read the number, close."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _client_for_db(monkeypatch: pytest.MonkeyPatch, host: str, port: int) -> AsyncClient:
    """App (no lifespan) whose engine is built from Settings pointing at host:port."""
    assert TEST_DATABASE_URL is not None
    monkeypatch.setenv("DB_HOST", host)
    monkeypatch.setenv("DB_PORT", str(port))
    monkeypatch.setenv("DB_NAME", TEST_DATABASE_URL.database or "")
    monkeypatch.setenv("DB_USER", TEST_DATABASE_URL.username or "")
    monkeypatch.setenv("DB_PASSWORD", TEST_DATABASE_URL.password or "")
    settings = Settings()  # type: ignore[call-arg]
    app = create_app()
    app.state.settings = settings
    app.state.db = build_engine(settings)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.integration
@pytest.mark.usefixtures("postgres_available")
async def test_readyz_200_against_real_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TEST_DATABASE_URL is not None
    async with _client_for_db(
        monkeypatch, TEST_DATABASE_URL.host or "", TEST_DATABASE_URL.port or 0
    ) as client:
        response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"db": "ok", "models": "ok"}}


@pytest.mark.integration
@pytest.mark.usefixtures("postgres_available")
async def test_readyz_503_within_5s_on_closed_port(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client_for_db(monkeypatch, "127.0.0.1", _closed_port()) as client:
        started = time.monotonic()
        response = await client.get("/readyz")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["checks"]["db"] == "fail"
    assert elapsed < CLOSED_PORT_BUDGET_S
