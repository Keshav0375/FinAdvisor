from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import UserClaims
from src.db.rls import set_rls_context

from .conftest import requires_db


@requires_db
@pytest.mark.asyncio
async def test_set_rls_context_sets_tier(db_session: AsyncSession) -> None:
    user = UserClaims(
        sub="sarah_chen",
        name="Sarah Chen",
        tier="senior",
        tier_level=3,
        jurisdictions=["US"],
        licenses=["Series-7", "Series-66"],
    )
    await set_rls_context(db_session, user)
    result = await db_session.execute(text("SELECT current_setting('app.user_tier')"))
    assert result.scalar() == "3"


@requires_db
@pytest.mark.asyncio
async def test_set_rls_context_sets_jurisdictions(db_session: AsyncSession) -> None:
    user = UserClaims(
        sub="priya_sharma",
        name="Priya Sharma",
        tier="advisor",
        tier_level=2,
        jurisdictions=["US", "EU"],
        licenses=["Series-7", "MiFID-II"],
    )
    await set_rls_context(db_session, user)
    result = await db_session.execute(text("SELECT current_setting('app.user_jurisdictions')"))
    assert result.scalar() == "US,EU"
