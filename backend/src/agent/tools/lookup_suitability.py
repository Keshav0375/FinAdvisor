from __future__ import annotations

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import UserClaims

log = structlog.get_logger()


class SuitabilityInput(BaseModel):
    product_category: str = Field(
        description="Product category: fixed_income, equity, alternatives, structured"
    )
    client_risk_profile: str = Field(
        description="Client risk profile: conservative, moderate, aggressive"
    )


class SuitabilityRuleResult(BaseModel):
    rule_name: str
    product_category: str
    client_risk_profile: str
    jurisdiction: str
    regulatory_ref: str
    rule_text: str
    last_reviewed_at: str


class SuitabilityOutput(BaseModel):
    rules: list[SuitabilityRuleResult]
    total_found: int


SUITABILITY_TOOL = {
    "name": "lookup_suitability_rule",
    "description": (
        "Look up the firm's suitability rules for a specific product category "
        "and client risk profile. Returns applicable rules with regulatory refs."
    ),
    "input_schema": SuitabilityInput.model_json_schema(),
}


async def lookup_suitability_rule(
    input_data: SuitabilityInput,
    session: AsyncSession,
    user: UserClaims,
) -> SuitabilityOutput:
    log.info(
        "suitability_lookup_start",
        product_category=input_data.product_category,
        client_risk_profile=input_data.client_risk_profile,
        user=user.sub,
    )

    jurisdictions = user.jurisdictions
    result = await session.execute(
        text("""
            SELECT rule_name, product_category, client_risk_profile,
                   jurisdiction, regulatory_ref, rule_text, last_reviewed_at
            FROM suitability_rules
            WHERE product_category = :category
              AND client_risk_profile = :risk_profile
              AND jurisdiction = ANY(:jurisdictions)
              AND min_tier_required <= :tier
            ORDER BY jurisdiction, rule_name
        """),
        {
            "category": input_data.product_category,
            "risk_profile": input_data.client_risk_profile,
            "jurisdictions": jurisdictions,
            "tier": user.tier_level,
        },
    )
    rows = result.fetchall()

    rules = [
        SuitabilityRuleResult(
            rule_name=row.rule_name,
            product_category=row.product_category,
            client_risk_profile=row.client_risk_profile,
            jurisdiction=row.jurisdiction,
            regulatory_ref=row.regulatory_ref,
            rule_text=row.rule_text,
            last_reviewed_at=str(row.last_reviewed_at),
        )
        for row in rows
    ]

    log.info("suitability_lookup_complete", rules_found=len(rules))
    return SuitabilityOutput(rules=rules, total_found=len(rules))
