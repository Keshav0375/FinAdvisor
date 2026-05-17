from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import UserClaims
from src.db.rls import set_rls_context

from .conftest import ALEX_KIM, JAMES_WRIGHT, PRIYA_SHARMA, SARAH_CHEN, requires_db

QUERY_VECTOR = str([0.01] * 1024)


async def vector_similarity_search(
    session: AsyncSession,
    query_vec: str,
    top_k: int = 200,
) -> list[Any]:
    result = await session.execute(
        text("""
            SELECT content, jurisdiction, tier_required, source_title,
                   embedding <=> cast(:query_vec as vector) AS distance
            FROM chunks
            ORDER BY embedding <=> cast(:query_vec as vector)
            LIMIT :top_k
        """),
        {"query_vec": query_vec, "top_k": top_k},
    )
    return result.fetchall()


def assert_zero_leakage(rows: list[Any], user: UserClaims) -> None:
    for row in rows:
        assert row.tier_required <= user.tier_level, (
            f"LEAKAGE: chunk tier {row.tier_required} > user tier {user.tier_level}"
        )
        assert row.jurisdiction in user.jurisdictions, (
            f"LEAKAGE: jurisdiction '{row.jurisdiction}' not in {user.jurisdictions}"
        )


@requires_db
@pytest.mark.asyncio
async def test_sarah_chen_vector_search(live_session: AsyncSession) -> None:
    """sarah_chen (US/tier-3): vector search returns only US tier 1-3 chunks."""
    await set_rls_context(live_session, SARAH_CHEN)
    rows = await vector_similarity_search(live_session, QUERY_VECTOR)

    assert len(rows) > 0
    assert_zero_leakage(rows, SARAH_CHEN)

    jurisdictions = {r.jurisdiction for r in rows}
    tiers = {r.tier_required for r in rows}
    assert jurisdictions == {"US"}
    assert tiers == {1, 2, 3}
    assert 4 not in tiers


@requires_db
@pytest.mark.asyncio
async def test_alex_kim_vector_search(live_session: AsyncSession) -> None:
    """alex_kim (EU/tier-1): vector search returns only EU tier-1 chunks."""
    await set_rls_context(live_session, ALEX_KIM)
    rows = await vector_similarity_search(live_session, QUERY_VECTOR)

    assert len(rows) > 0
    assert_zero_leakage(rows, ALEX_KIM)

    jurisdictions = {r.jurisdiction for r in rows}
    tiers = {r.tier_required for r in rows}
    assert jurisdictions == {"EU"}
    assert tiers == {1}


@requires_db
@pytest.mark.asyncio
async def test_james_wright_vector_search(live_session: AsyncSession) -> None:
    """james_wright (UK/tier-4): vector search returns UK tier 1-4 chunks."""
    await set_rls_context(live_session, JAMES_WRIGHT)
    rows = await vector_similarity_search(live_session, QUERY_VECTOR)

    assert len(rows) > 0
    assert_zero_leakage(rows, JAMES_WRIGHT)

    jurisdictions = {r.jurisdiction for r in rows}
    tiers = {r.tier_required for r in rows}
    assert jurisdictions == {"UK"}
    assert tiers == {1, 2, 3, 4}


@requires_db
@pytest.mark.asyncio
async def test_priya_sharma_vector_search(live_session: AsyncSession) -> None:
    """priya_sharma (US+EU/tier-2): vector search returns US+EU tier 1-2 chunks."""
    await set_rls_context(live_session, PRIYA_SHARMA)
    rows = await vector_similarity_search(live_session, QUERY_VECTOR)

    assert len(rows) > 0
    assert_zero_leakage(rows, PRIYA_SHARMA)

    jurisdictions = {r.jurisdiction for r in rows}
    tiers = {r.tier_required for r in rows}
    assert jurisdictions == {"US", "EU"}
    assert tiers <= {1, 2}


@requires_db
@pytest.mark.asyncio
async def test_different_users_see_different_results(live_session: AsyncSession) -> None:
    """Each user gets a different result set — RLS is not returning the same data."""
    counts: dict[str, int] = {}
    users = [
        ("sarah_chen", SARAH_CHEN),
        ("alex_kim", ALEX_KIM),
        ("james_wright", JAMES_WRIGHT),
        ("priya_sharma", PRIYA_SHARMA),
    ]

    for name, user in users:
        await set_rls_context(live_session, user)
        rows = await vector_similarity_search(live_session, QUERY_VECTOR)
        counts[name] = len(rows)

    unique_counts = set(counts.values())
    assert len(unique_counts) == len(counts), (
        f"Expected all users to see different counts, got: {counts}"
    )
