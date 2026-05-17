from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.tools.lookup_factsheet import (
    FactsheetInput,
    FactsheetOutput,
    lookup_product_factsheet,
)

from .conftest import ALEX_KIM, SARAH_CHEN, requires_db


@requires_db
@pytest.mark.asyncio
async def test_exact_name_match(live_session: AsyncSession) -> None:
    input_data = FactsheetInput(product_name="Meridian Core Bond Fund")
    result = await lookup_product_factsheet(input_data, live_session, SARAH_CHEN)

    assert isinstance(result, FactsheetOutput)
    assert result.total_found == 1
    assert result.results[0].title == "Meridian Core Bond Fund"
    assert result.results[0].jurisdiction == "US"
    assert len(result.results[0].content) > 0


@requires_db
@pytest.mark.asyncio
async def test_partial_name_match(live_session: AsyncSession) -> None:
    input_data = FactsheetInput(product_name="Bond")
    result = await lookup_product_factsheet(input_data, live_session, SARAH_CHEN)

    assert result.total_found >= 2
    for r in result.results:
        assert "bond" in r.title.lower()
        assert r.jurisdiction == "US"
        assert r.tier_required <= 3


@requires_db
@pytest.mark.asyncio
async def test_case_insensitive_match(live_session: AsyncSession) -> None:
    input_data = FactsheetInput(product_name="meridian core bond")
    result = await lookup_product_factsheet(input_data, live_session, SARAH_CHEN)

    assert result.total_found == 1
    assert result.results[0].title == "Meridian Core Bond Fund"


@requires_db
@pytest.mark.asyncio
async def test_jurisdiction_filtering(live_session: AsyncSession) -> None:
    input_data = FactsheetInput(product_name="Meridian")
    us_result = await lookup_product_factsheet(input_data, live_session, SARAH_CHEN)
    eu_result = await lookup_product_factsheet(input_data, live_session, ALEX_KIM)

    us_jurisdictions = {r.jurisdiction for r in us_result.results}
    eu_jurisdictions = {r.jurisdiction for r in eu_result.results}

    assert us_jurisdictions == {"US"}
    assert eu_jurisdictions == {"EU"}
    assert us_result.total_found != eu_result.total_found


@requires_db
@pytest.mark.asyncio
async def test_tier_filtering_excludes_high_tier(live_session: AsyncSession) -> None:
    input_data = FactsheetInput(product_name="Hedge Fund")
    result_alex = await lookup_product_factsheet(input_data, live_session, ALEX_KIM)
    assert result_alex.total_found == 0

    result_sarah = await lookup_product_factsheet(input_data, live_session, SARAH_CHEN)
    assert result_sarah.total_found == 0


@requires_db
@pytest.mark.asyncio
async def test_no_match(live_session: AsyncSession) -> None:
    input_data = FactsheetInput(product_name="Nonexistent XYZ Product")
    result = await lookup_product_factsheet(input_data, live_session, SARAH_CHEN)

    assert result.total_found == 0
    assert result.results == []
