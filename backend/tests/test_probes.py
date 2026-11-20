from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from types import TracebackType
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.config import Settings
from src.main import create_app

DB_HOST = "db.test.invalid"
RETIRED_ID = "claude-sonnet-4-20250514"
DEPRECATED_ID = "claude-haiku-4-5"
SECRET_MARKER = "boom-secret-marker-7731"


class StubSession:
    """Async session stand-in: records the statement and optionally fails or hangs."""

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.statements: list[str] = []

    async def __aenter__(self) -> StubSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))
        if self.behaviour == "raise":
            raise ConnectionRefusedError(f"could not connect to {DB_HOST}: {SECRET_MARKER}")
        if self.behaviour == "hang":
            await asyncio.sleep(30)


class StubFactory:
    def __init__(self, behaviour: str = "ok") -> None:
        self.session = StubSession(behaviour)

    def __call__(self) -> StubSession:
        return self.session


@pytest.fixture
async def client_for() -> AsyncGenerator[Any, None]:
    """Build a fresh app (no lifespan) with state set by the test, yield a client factory."""
    clients: list[AsyncClient] = []

    def make(behaviour: str | None = "ok") -> tuple[AsyncClient, StubFactory | None]:
        app = create_app()
        app.state.settings = Settings()  # type: ignore[call-arg]
        factory = StubFactory(behaviour) if behaviour is not None else None
        if factory is not None:
            app.state.db = factory
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client, factory

    yield make
    for client in clients:
        await client.aclose()


async def test_readyz_ok_with_stub_db_and_default_models(client_for: Any) -> None:
    client, factory = client_for("ok")
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"db": "ok", "models": "ok"}}
    assert factory is not None
    assert factory.session.statements == ["SELECT 1"]


async def test_readyz_fails_on_retired_model_and_healthz_stays_up(
    client_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL_ANSWER", RETIRED_ID)
    client, _ = client_for("ok")
    response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"db": "ok", "models": "fail"}
    health = await client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


async def test_readyz_ok_when_model_only_deprecated(
    client_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL_AUX", DEPRECATED_ID)
    client, _ = client_for("ok")
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["checks"]["models"] == "ok"


async def test_readyz_db_error_leaks_nothing(client_for: Any) -> None:
    client, _ = client_for("raise")
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"db": "fail", "models": "ok"}}
    assert DB_HOST not in response.text
    assert SECRET_MARKER not in response.text
    assert "could not connect" not in response.text


async def test_readyz_db_timeout_fails_fast(
    client_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("READINESS_TIMEOUT_S", "0.05")
    client, _ = client_for("hang")
    started = time.monotonic()
    response = await client.get("/readyz")
    assert time.monotonic() - started < 5
    assert response.status_code == 503
    assert response.json()["checks"]["db"] == "fail"


async def test_readyz_reports_both_failures(
    client_for: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL_ANSWER", RETIRED_ID)
    client, _ = client_for("raise")
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"db": "fail", "models": "fail"}


async def test_healthz_touches_no_dependency(client_for: Any) -> None:
    client, _ = client_for(None)
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_api_health_route_is_kept() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200


def test_readiness_timeout_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().readiness_timeout_s == 2.0  # type: ignore[call-arg]
    monkeypatch.setenv("READINESS_TIMEOUT_S", "0.5")
    assert Settings().readiness_timeout_s == 0.5  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_readiness_timeout_must_be_positive(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("READINESS_TIMEOUT_S", bad)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
