from __future__ import annotations

import json

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

from src.dependencies import get_redactor
from src.errors import (
    AuthorizationError,
    EvalThresholdError,
    FinAdvisorError,
    RetrievalError,
    ToolExecutionError,
)
from src.main import app
from src.observability.logging import configure_logging
from src.pii.fallback import RegexRedactor


def test_configure_logging_json() -> None:
    configure_logging(json_output=True)
    logger = structlog.get_logger()
    assert logger is not None


def test_configure_logging_console() -> None:
    configure_logging(json_output=False)
    logger = structlog.get_logger()
    assert logger is not None


def test_json_output_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_output=True)
    structlog.contextvars.clear_contextvars()
    logger = structlog.get_logger()
    logger.info("test_event", key="value")

    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert parsed["event"] == "test_event"
    assert parsed["key"] == "value"
    assert "timestamp" in parsed
    assert parsed["level"] == "info"


def test_contextvars_binding(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_output=True)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-123", user_id="sarah_chen")
    logger = structlog.get_logger()
    logger.info("bound_event")

    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert parsed["request_id"] == "req-123"
    assert parsed["user_id"] == "sarah_chen"
    structlog.contextvars.clear_contextvars()


def test_error_hierarchy() -> None:
    assert issubclass(AuthorizationError, FinAdvisorError)
    assert issubclass(RetrievalError, FinAdvisorError)
    assert issubclass(ToolExecutionError, FinAdvisorError)
    assert issubclass(EvalThresholdError, FinAdvisorError)
    assert issubclass(FinAdvisorError, Exception)


@pytest.mark.asyncio
async def test_authorization_error_returns_403() -> None:

    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-authz-error")
    async def trigger_authz_error() -> None:
        raise AuthorizationError("Not allowed")

    app.include_router(test_router, prefix="/api")

    mock_redactor = RegexRedactor()
    app.dependency_overrides[get_redactor] = lambda: mock_redactor

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/test-authz-error")
        assert resp.status_code == 403
        data = resp.json()
        assert data["error"] == "AuthorizationError"
        assert "Not allowed" in data["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_retrieval_error_returns_502() -> None:
    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-retrieval-error")
    async def trigger_retrieval_error() -> None:
        raise RetrievalError("Vector store unreachable")

    app.include_router(test_router, prefix="/api")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/test-retrieval-error")
        assert resp.status_code == 502
        data = resp.json()
        assert data["error"] == "RetrievalError"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_tool_execution_error_returns_500() -> None:
    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-tool-error")
    async def trigger_tool_error() -> None:
        raise ToolExecutionError("Tool failed")

    app.include_router(test_router, prefix="/api")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/test-tool-error")
        assert resp.status_code == 500
        data = resp.json()
        assert data["error"] == "ToolExecutionError"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unhandled_error_returns_500_generic() -> None:
    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-unhandled-error")
    async def trigger_unhandled() -> None:
        raise RuntimeError("Something unexpected")

    app.include_router(test_router, prefix="/api")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/test-unhandled-error")
        assert resp.status_code == 500
        data = resp.json()
        assert data["error"] == "InternalServerError"
        assert "unexpected" in data["detail"].lower()
        assert "Something unexpected" not in data["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_request_context_middleware_sets_request_id() -> None:
    mock_redactor = RegexRedactor()
    app.dependency_overrides[get_redactor] = lambda: mock_redactor

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/health",
                headers={"X-Request-Id": "custom-req-id"},
            )
        assert resp.headers.get("X-Request-Id") == "custom-req-id"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_request_context_middleware_generates_request_id() -> None:
    mock_redactor = RegexRedactor()
    app.dependency_overrides[get_redactor] = lambda: mock_redactor

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
        request_id = resp.headers.get("X-Request-Id")
        assert request_id is not None
        assert len(request_id) > 0
    finally:
        app.dependency_overrides.clear()
