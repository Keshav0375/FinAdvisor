from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.models import UserClaims
from src.db.rls import set_rls_context

DATABASE_URL = "postgresql+asyncpg://finadvisor:localdev@localhost:5432/finadvisor"


@pytest.fixture
async def async_session() -> AsyncSession:  # type: ignore[misc]
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_set_rls_context_sets_tier(async_session: AsyncSession) -> None:
    user = UserClaims(
        sub="sarah_chen",
        name="Sarah Chen",
        tier="senior",
        tier_level=3,
        jurisdictions=["US"],
        licenses=["Series-7", "Series-66"],
    )
    await set_rls_context(async_session, user)
    result = await async_session.execute(text("SELECT current_setting('app.user_tier')"))
    assert result.scalar() == "3"


@pytest.mark.asyncio
async def test_set_rls_context_sets_jurisdictions(async_session: AsyncSession) -> None:
    user = UserClaims(
        sub="priya_sharma",
        name="Priya Sharma",
        tier="advisor",
        tier_level=2,
        jurisdictions=["US", "EU"],
        licenses=["Series-7", "MiFID-II"],
    )
    await set_rls_context(async_session, user)
    result = await async_session.execute(text("SELECT current_setting('app.user_jurisdictions')"))
    assert result.scalar() == "US,EU"
