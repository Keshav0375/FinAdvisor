from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import anthropic
import structlog
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.orchestrator import FinAdvisorAgent
from src.agent.tools import ToolRegistry
from src.auth.jwt import get_current_user
from src.auth.models import UserClaims
from src.config import Settings
from src.db.rls import set_rls_context
from src.pii.fallback import RegexRedactor
from src.retrieval.embeddings import VoyageEmbeddings

log = structlog.get_logger()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = request.app.state.db
    async with session_factory() as session:
        yield session


async def get_rls_session(
    session: AsyncSession = Depends(get_db_session),
    user: UserClaims = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    await set_rls_context(session, user)
    yield session


def get_embeddings(request: Request) -> VoyageEmbeddings:
    return request.app.state.embeddings  # type: ignore[no-any-return]


def get_redactor(request: Request) -> RegexRedactor:
    return request.app.state.redactor  # type: ignore[no-any-return]


def get_llm_client(request: Request) -> anthropic.AsyncAnthropic:
    return request.app.state.llm_client  # type: ignore[no-any-return]


def get_langfuse(request: Request) -> Any | None:
    return getattr(request.app.state, "langfuse", None)


async def get_agent(
    session: AsyncSession = Depends(get_rls_session),
    embeddings: VoyageEmbeddings = Depends(get_embeddings),
    client: anthropic.AsyncAnthropic = Depends(get_llm_client),
    langfuse: Any = Depends(get_langfuse),
) -> FinAdvisorAgent:
    tool_registry = ToolRegistry(session=session, embeddings=embeddings)
    return FinAdvisorAgent(
        client=client,
        tool_registry=tool_registry,
        langfuse=langfuse,
    )
