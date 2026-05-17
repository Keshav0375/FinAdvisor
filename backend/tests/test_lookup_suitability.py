from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.tools.lookup_suitability import (
    SuitabilityInput,
    SuitabilityOutput,
    lookup_suitability_rule,
)

from .conftest import ALEX_KIM, PRIYA_SHARMA, SARAH_CHEN, requires_db

SEED_RULES: list[dict[str, Any]] = [
    {
        "rule_name": "US Fixed Income Conservative",
        "product_category": "fixed_income",
        "client_risk_profile": "conservative",
        "min_tier_required": 1,
        "jurisdiction": "US",
        "regulatory_ref": "FINRA Rule 2111",
        "rule_text": "Conservative clients should be limited to investment-grade bonds.",
        "last_reviewed_at": date(2025, 3, 15),
    },
    {
        "rule_name": "US Equity Aggressive",
        "product_category": "equity",
        "client_risk_profile": "aggressive",
        "min_tier_required": 2,
        "jurisdiction": "US",
        "regulatory_ref": "FINRA Rule 2111",
        "rule_text": "Aggressive equity strategies require tier-2 or above.",
        "last_reviewed_at": date(2025, 1, 10),
    },
    {
        "rule_name": "EU Fixed Income Conservative",
        "product_category": "fixed_income",
        "client_risk_profile": "conservative",
        "min_tier_required": 1,
        "jurisdiction": "EU",
        "regulatory_ref": "MiFID II Article 25",
        "rule_text": "EU conservative clients restricted to UCITS-compliant bond funds.",
        "last_reviewed_at": date(2025, 2, 20),
    },
    {
        "rule_name": "US Alternatives Moderate Tier3",
        "product_category": "alternatives",
        "client_risk_profile": "moderate",
        "min_tier_required": 3,
        "jurisdiction": "US",
        "regulatory_ref": "SEC Rule 506(b)",
        "rule_text": "Alternative investments require senior advisor tier or above.",
        "last_reviewed_at": date(2024, 12, 1),
    },
]


@pytest.fixture
async def rules_session(db_session: AsyncSession) -> AsyncSession:
    for rule in SEED_RULES:
        await db_session.execute(
            text("""
                INSERT INTO suitability_rules
                    (rule_name, product_category, client_risk_profile,
                     min_tier_required, jurisdiction, regulatory_ref,
                     rule_text, last_reviewed_at)
                VALUES (:rule_name, :product_category, :client_risk_profile,
                        :min_tier_required, :jurisdiction, :regulatory_ref,
                        :rule_text, :last_reviewed_at)
            """),
            rule,
        )
    await db_session.commit()
    yield db_session
    await db_session.execute(text("DELETE FROM suitability_rules"))
    await db_session.commit()


@requires_db
@pytest.mark.asyncio
async def test_us_user_finds_matching_rule(rules_session: AsyncSession) -> None:
    input_data = SuitabilityInput(
        product_category="fixed_income", client_risk_profile="conservative"
    )
    result = await lookup_suitability_rule(input_data, rules_session, SARAH_CHEN)

    assert isinstance(result, SuitabilityOutput)
    assert result.total_found == 1
    assert result.rules[0].rule_name == "US Fixed Income Conservative"
    assert result.rules[0].regulatory_ref == "FINRA Rule 2111"
    assert result.rules[0].jurisdiction == "US"


@requires_db
@pytest.mark.asyncio
async def test_eu_user_sees_only_eu_rules(rules_session: AsyncSession) -> None:
    input_data = SuitabilityInput(
        product_category="fixed_income", client_risk_profile="conservative"
    )
    result = await lookup_suitability_rule(input_data, rules_session, ALEX_KIM)

    assert result.total_found == 1
    assert result.rules[0].jurisdiction == "EU"
    assert result.rules[0].regulatory_ref == "MiFID II Article 25"


@requires_db
@pytest.mark.asyncio
async def test_multi_jurisdiction_user_sees_both(rules_session: AsyncSession) -> None:
    input_data = SuitabilityInput(
        product_category="fixed_income", client_risk_profile="conservative"
    )
    result = await lookup_suitability_rule(input_data, rules_session, PRIYA_SHARMA)

    assert result.total_found == 2
    jurisdictions = {r.jurisdiction for r in result.rules}
    assert jurisdictions == {"US", "EU"}


@requires_db
@pytest.mark.asyncio
async def test_tier_filter_excludes_high_tier_rules(rules_session: AsyncSession) -> None:
    input_data = SuitabilityInput(product_category="alternatives", client_risk_profile="moderate")
    result_low_tier = await lookup_suitability_rule(input_data, rules_session, PRIYA_SHARMA)
    assert result_low_tier.total_found == 0

    result_high_tier = await lookup_suitability_rule(input_data, rules_session, SARAH_CHEN)
    assert result_high_tier.total_found == 1
    assert result_high_tier.rules[0].rule_name == "US Alternatives Moderate Tier3"


@requires_db
@pytest.mark.asyncio
async def test_no_matching_rules(rules_session: AsyncSession) -> None:
    input_data = SuitabilityInput(product_category="structured", client_risk_profile="conservative")
    result = await lookup_suitability_rule(input_data, rules_session, SARAH_CHEN)

    assert result.total_found == 0
    assert result.rules == []
