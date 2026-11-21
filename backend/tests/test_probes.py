from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from types import TracebackType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.api import probes
from src.api.probes import ReadinessCache
from src.config import Settings
from src.main import create_app, lifespan

DB_HOST = "db.test.invalid"
RETIRED_ID = "claude-sonnet-4-20250514"
DEPRECATED_ID = "claude-haiku-4-5"
RETIRING_ID = "claude-sonnet-4-5"
SECRET_MARKER = "boom-secret-marker-7731"  # gitleaks:allow


SLOW_YIELDS = 50
CONCURRENT_CALLS = 10


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
        if self.behaviour == "slow":
            # Yield to the loop many times (no real sleep) so concurrent requests overlap.
            for _ in range(SLOW_YIELDS):
                await asyncio.sleep(0)


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


def _app_with(behaviour: str, clock: Any = None) -> tuple[Any, StubFactory]:
    app = create_app()
    app.state.settings = Settings()  # type: ignore[call-arg]
    factory = StubFactory(behaviour)
    app.state.db = factory
    if clock is not None:
        app.state.readiness_cache = ReadinessCache(clock=clock)
    return app, factory


async def test_concurrent_readyz_calls_share_one_db_probe() -> None:
    app, factory = _app_with("slow")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*(client.get("/readyz") for _ in range(CONCURRENT_CALLS)))
    assert [r.status_code for r in responses] == [200] * CONCURRENT_CALLS
    assert factory.session.statements == ["SELECT 1"]


async def test_readyz_result_is_cached_until_ttl_then_probed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READINESS_CACHE_TTL_S", "5")
    now = [100.0]
    app, factory = _app_with("ok", clock=lambda: now[0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/readyz")
        now[0] += 4.9
        await client.get("/readyz")
        assert len(factory.session.statements) == 1
        now[0] += 0.2
        await client.get("/readyz")
    assert len(factory.session.statements) == 2


async def test_failing_readyz_result_is_cached_for_the_ttl_too() -> None:
    now = [0.0]
    app, factory = _app_with("raise", clock=lambda: now[0])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/readyz")
        second = await client.get("/readyz")
    assert first.status_code == second.status_code == 503
    assert second.json()["checks"]["db"] == "fail"
    assert len(factory.session.statements) == 1


async def test_readiness_cache_lives_on_app_state_not_shared_between_apps() -> None:
    first, first_factory = _app_with("ok")
    second, second_factory = _app_with("ok")
    for app in (first, second):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/readyz")
    assert len(first_factory.session.statements) == 1
    assert len(second_factory.session.statements) == 1
    assert first.state.readiness_cache is not second.state.readiness_cache


@pytest.mark.parametrize(
    ("today", "expected_status"),
    [(date(2026, 1, 1), 200), (date(2026, 11, 15), 503)],
)
async def test_readyz_model_check_uses_utc_today(
    monkeypatch: pytest.MonkeyPatch, today: date, expected_status: int
) -> None:
    # RETIRING_ID retires 2026-11-30: fine in January, retiring_soon in mid-November.
    monkeypatch.setenv("LLM_MODEL_ANSWER", RETIRING_ID)
    monkeypatch.setattr(probes, "_utc_today", lambda: today)
    app, _ = _app_with("ok")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/readyz")
    assert response.status_code == expected_status


def test_utc_today_is_a_utc_date() -> None:
    before = datetime.now(UTC).date()
    assert before <= probes._utc_today() <= datetime.now(UTC).date()


async def test_app_starts_with_retired_model_and_readyz_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL_ANSWER", RETIRED_ID)
    app = create_app()
    factory = StubFactory("ok")
    with (
        patch("src.main.build_engine", return_value=factory),
        patch("src.main.VoyageEmbeddings", return_value=MagicMock()),
        patch("src.main.create_redactor", return_value=MagicMock()),
    ):
        async with lifespan(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                ready = await client.get("/readyz")
                live = await client.get("/healthz")
    assert ready.status_code == 503
    assert ready.json()["checks"] == {"db": "ok", "models": "fail"}
    assert live.status_code == 200


def test_readiness_cache_ttl_default_override_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().readiness_cache_ttl_s == 5.0  # type: ignore[call-arg]
    monkeypatch.setenv("READINESS_CACHE_TTL_S", "1.5")
    assert Settings().readiness_cache_ttl_s == 1.5  # type: ignore[call-arg]
    monkeypatch.setenv("READINESS_CACHE_TTL_S", "0")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
