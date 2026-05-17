from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.rls import set_rls_context

from .conftest import ALEX_KIM, PRIYA_SHARMA, SARAH_CHEN, requires_db


@requires_db
@pytest.mark.asyncio
async def test_tier3_user_sees_tier_1_2_3_not_4(seeded_session: AsyncSession) -> None:
    """Tier-3 user (sarah_chen) sees tier-1, 2, 3 chunks but not tier-4."""
    await set_rls_context(seeded_session, SARAH_CHEN)
    result = await seeded_session.execute(text("SELECT content, tier_required FROM chunks"))
    rows = result.fetchall()
    tiers = {row.tier_required for row in rows}
    assert 1 in tiers
    assert 2 in tiers
    assert 3 in tiers
    assert 4 not in tiers
    # All should be US jurisdiction (sarah's only jurisdiction)
    jurisdictions = {row.content.split()[0] for row in rows}
    assert jurisdictions == {"US"}


@requires_db
@pytest.mark.asyncio
async def test_eu_only_user_sees_eu_chunks_not_us(seeded_session: AsyncSession) -> None:
    """EU-only user (alex_kim, tier-1) sees only EU tier-1 chunks."""
    await set_rls_context(seeded_session, ALEX_KIM)
    result = await seeded_session.execute(
        text("SELECT content, jurisdiction, tier_required FROM chunks")
    )
    rows = result.fetchall()
    assert len(rows) == 1
    assert rows[0].jurisdiction == "EU"
    assert rows[0].tier_required == 1


@requires_db
@pytest.mark.asyncio
async def test_combined_tier_and_jurisdiction_filter(seeded_session: AsyncSession) -> None:
    """Multi-jurisdiction tier-2 user (priya_sharma) sees US+EU tier-1,2 chunks."""
    await set_rls_context(seeded_session, PRIYA_SHARMA)
    result = await seeded_session.execute(
        text("SELECT content, jurisdiction, tier_required FROM chunks")
    )
    rows = result.fetchall()
    jurisdictions = {row.jurisdiction for row in rows}
    tiers = {row.tier_required for row in rows}
    # Should see US and EU
    assert jurisdictions == {"US", "EU"}
    # Should see tier 1 and 2, not 3 or 4
    assert tiers <= {1, 2}
    assert 3 not in tiers
    assert 4 not in tiers
    # Should see 4 chunks: US-1, US-2, EU-1, EU-2
    assert len(rows) == 4
