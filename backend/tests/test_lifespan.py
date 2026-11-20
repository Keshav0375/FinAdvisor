from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from src.main import lifespan

CUSTOM_BASE_URL = "https://egress.test.invalid"


async def _enter_lifespan() -> FastAPI:
    app = FastAPI()
    # DB engine, Voyage and the redactor are not under test here; no network is touched.
    with (
        patch("src.main.build_engine", return_value=MagicMock()),
        patch("src.main.VoyageEmbeddings", return_value=MagicMock()),
        patch("src.main.create_redactor", return_value=MagicMock()),
    ):
        async with lifespan(app):
            pass
    return app


@pytest.mark.asyncio
async def test_lifespan_client_uses_configured_base_url_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", CUSTOM_BASE_URL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "lifespan-test-key")

    app = await _enter_lifespan()

    client = app.state.llm_client
    assert str(client.base_url).rstrip("/") == CUSTOM_BASE_URL
    assert client.api_key == "lifespan-test-key"


@pytest.mark.asyncio
async def test_lifespan_client_defaults_to_direct_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    app = await _enter_lifespan()

    assert str(app.state.llm_client.base_url).rstrip("/") == "https://api.anthropic.com"
