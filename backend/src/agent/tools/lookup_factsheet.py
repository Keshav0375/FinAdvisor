from __future__ import annotations

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import UserClaims

log = structlog.get_logger()


class FactsheetInput(BaseModel):
    product_name: str = Field(description="Product name or ticker to look up")


class FactsheetResult(BaseModel):
    title: str
    jurisdiction: str
    tier_required: int
    regulatory_ref: str | None
    last_reviewed_at: str
    product_category: str | None
    risk_level: str | None
    content: str


class FactsheetOutput(BaseModel):
    results: list[FactsheetResult]
    total_found: int


FACTSHEET_TOOL = {
    "name": "lookup_product_factsheet",
    "description": (
        "Retrieve the full product fact sheet for a specific investment product. "
        "Returns product details, risk classification, and regulatory information."
    ),
    "input_schema": FactsheetInput.model_json_schema(),
}


async def lookup_product_factsheet(
    input_data: FactsheetInput,
    session: AsyncSession,
    user: UserClaims,
) -> FactsheetOutput:
    log.info(
        "factsheet_lookup_start",
        product_name=input_data.product_name,
        user=user.sub,
    )

    search_pattern = f"%{input_data.product_name}%"
    result = await session.execute(
        text("""
            SELECT title, jurisdiction, tier_required, regulatory_ref,
                   last_reviewed_at, product_category, risk_level, raw_content
            FROM documents
            WHERE doc_type = 'product_factsheet'
              AND title ILIKE :pattern
              AND jurisdiction = ANY(:jurisdictions)
              AND tier_required <= :tier
            ORDER BY title
        """),
        {
            "pattern": search_pattern,
            "jurisdictions": user.jurisdictions,
            "tier": user.tier_level,
        },
    )
    rows = result.fetchall()

    results = [
        FactsheetResult(
            title=row.title,
            jurisdiction=row.jurisdiction,
            tier_required=row.tier_required,
            regulatory_ref=row.regulatory_ref,
            last_reviewed_at=str(row.last_reviewed_at),
            product_category=row.product_category,
            risk_level=row.risk_level,
            content=row.raw_content,
        )
        for row in rows
    ]

    log.info("factsheet_lookup_complete", results_count=len(results))
    return FactsheetOutput(results=results, total_found=len(results))
