from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.rls import set_rls_context

from .conftest import (
    ALEX_KIM,
    PRIYA_SHARMA,
    SARAH_CHEN,
    SEED_CONTENT_PREFIX,
    requires_db,
)


@requires_db
@pytest.mark.asyncio
async def test_tier3_user_sees_tier_1_2_3_not_4(seeded_session: AsyncSession) -> None:
    await set_rls_context(seeded_session, SARAH_CHEN)
    result = await seeded_session.execute(
        text("SELECT content, jurisdiction, tier_required FROM chunks WHERE content LIKE :prefix"),
        {"prefix": f"{SEED_CONTENT_PREFIX}%"},
    )
    rows = result.fetchall()
    tiers = {row.tier_required for row in rows}
    jurisdictions = {row.jurisdiction for row in rows}
    assert 1 in tiers
    assert 2 in tiers
    assert 3 in tiers
    assert 4 not in tiers
    assert jurisdictions == {"US"}
    assert len(rows) == 3


@requires_db
@pytest.mark.asyncio
async def test_eu_only_user_sees_eu_chunks_not_us(seeded_session: AsyncSession) -> None:
    await set_rls_context(seeded_session, ALEX_KIM)
    result = await seeded_session.execute(
        text("SELECT content, jurisdiction, tier_required FROM chunks WHERE content LIKE :prefix"),
        {"prefix": f"{SEED_CONTENT_PREFIX}%"},
    )
    rows = result.fetchall()
    assert len(rows) == 1
    assert rows[0].jurisdiction == "EU"
    assert rows[0].tier_required == 1


@requires_db
@pytest.mark.asyncio
async def test_combined_tier_and_jurisdiction_filter(seeded_session: AsyncSession) -> None:
    await set_rls_context(seeded_session, PRIYA_SHARMA)
    result = await seeded_session.execute(
        text("SELECT content, jurisdiction, tier_required FROM chunks WHERE content LIKE :prefix"),
        {"prefix": f"{SEED_CONTENT_PREFIX}%"},
    )
    rows = result.fetchall()
    jurisdictions = {row.jurisdiction for row in rows}
    tiers = {row.tier_required for row in rows}
    assert jurisdictions == {"US", "EU"}
    assert tiers <= {1, 2}
    assert 3 not in tiers
    assert 4 not in tiers
    assert len(rows) == 4
