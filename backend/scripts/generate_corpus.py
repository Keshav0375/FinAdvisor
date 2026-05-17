"""Synthetic corpus generator for FinAdvisor.

Uses Claude API to generate 50 financial documents across 4 categories.
Output: JSON files in data/corpus/{doc_type}/ directories.

Usage:
    python scripts/generate_corpus.py
    python scripts/generate_corpus.py --dry-run  # Print plan without generating
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import anthropic
import structlog

from src.config import Settings

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"

DOCUMENT_SPECS: list[dict[str, Any]] = [
    # 20 Product Factsheets (tiers 1-4, US/EU/UK)
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Core Bond Fund",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "conservative",
        "regulatory_ref": "FINRA Rule 2111",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian US Equity Growth Fund",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": "equity",
        "risk_level": "aggressive",
        "regulatory_ref": "FINRA Rule 2111, SEC Rule 17a-4",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Balanced Income Portfolio",
        "jurisdiction": "US",
        "tier_required": 2,
        "product_category": "fixed_income",
        "risk_level": "moderate",
        "regulatory_ref": "FINRA Rule 2111",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Private Credit Opportunities",
        "jurisdiction": "US",
        "tier_required": 3,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "FINRA Rule 2111, SEC Regulation D",
        "stale": False,
        "pii": True,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Ultra HNW Real Estate Fund",
        "jurisdiction": "US",
        "tier_required": 4,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "SEC Regulation D, FINRA Rule 5110",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian EU Sovereign Bond ETF",
        "jurisdiction": "EU",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "conservative",
        "regulatory_ref": "MiFID II Article 25",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian European Small Cap Fund",
        "jurisdiction": "EU",
        "tier_required": 2,
        "product_category": "equity",
        "risk_level": "aggressive",
        "regulatory_ref": "MiFID II Article 25, UCITS Directive",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian EU Infrastructure Debt Fund",
        "jurisdiction": "EU",
        "tier_required": 3,
        "product_category": "alternatives",
        "risk_level": "moderate",
        "regulatory_ref": "MiFID II Article 25, AIFMD",
        "stale": False,
        "pii": True,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian EU Private Equity Co-Invest",
        "jurisdiction": "EU",
        "tier_required": 4,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "AIFMD Article 22, MiFID II",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian UK Gilt Fund",
        "jurisdiction": "UK",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "conservative",
        "regulatory_ref": "FCA COBS 9.2",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian UK Equity Income Trust",
        "jurisdiction": "UK",
        "tier_required": 2,
        "product_category": "equity",
        "risk_level": "moderate",
        "regulatory_ref": "FCA COBS 9.2, FCA PROD",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian UK Structured Notes Series A",
        "jurisdiction": "UK",
        "tier_required": 3,
        "product_category": "structured",
        "risk_level": "aggressive",
        "regulatory_ref": "FCA COBS 9.2, PRIIPs Regulation",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian UK Private Wealth Mandate",
        "jurisdiction": "UK",
        "tier_required": 4,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "FCA COBS 9.2, FCA CASS",
        "stale": False,
        "pii": True,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Global Multi-Asset Fund",
        "jurisdiction": "US",
        "tier_required": 2,
        "product_category": "equity",
        "risk_level": "moderate",
        "regulatory_ref": "FINRA Rule 2111, SEC 1940 Act",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Short Duration Treasury ETF",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "conservative",
        "regulatory_ref": "FINRA Rule 2111",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Emerging Markets Equity Fund",
        "jurisdiction": "EU",
        "tier_required": 2,
        "product_category": "equity",
        "risk_level": "aggressive",
        "regulatory_ref": "MiFID II Article 25, UCITS Directive",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian ESG Impact Bond Fund",
        "jurisdiction": "EU",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "moderate",
        "regulatory_ref": "SFDR Article 8, MiFID II Article 25",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian UK AIM Growth Portfolio",
        "jurisdiction": "UK",
        "tier_required": 2,
        "product_category": "equity",
        "risk_level": "aggressive",
        "regulatory_ref": "FCA COBS 9.2",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian US Municipal Bond Fund",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "conservative",
        "regulatory_ref": "MSRB Rule G-19, FINRA Rule 2111",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "product_factsheet",
        "title": "Meridian Hedge Fund Access Program",
        "jurisdiction": "US",
        "tier_required": 4,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "SEC Regulation D, FINRA Rule 2111",
        "stale": False,
        "pii": False,
    },
    # 10 Suitability Rules (tiers 1-3, US/EU/UK)
    {
        "doc_type": "suitability_rule",
        "title": "Conservative Client Fixed Income Rule",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "conservative",
        "regulatory_ref": "FINRA Rule 2111(a)",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "Aggressive Equity Suitability Standards",
        "jurisdiction": "US",
        "tier_required": 2,
        "product_category": "equity",
        "risk_level": "aggressive",
        "regulatory_ref": "FINRA Rule 2111(a), FINRA Rule 2090",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "Alternative Investment Qualification Rule",
        "jurisdiction": "US",
        "tier_required": 3,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "SEC Regulation D 506(b), FINRA Rule 2111",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "EU MiFID Suitability Assessment Framework",
        "jurisdiction": "EU",
        "tier_required": 1,
        "product_category": "equity",
        "risk_level": "moderate",
        "regulatory_ref": "MiFID II Article 25(2), ESMA Guidelines",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "EU Complex Product Appropriateness Test",
        "jurisdiction": "EU",
        "tier_required": 2,
        "product_category": "structured",
        "risk_level": "aggressive",
        "regulatory_ref": "MiFID II Article 25(3), PRIIPs KID Regulation",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "EU AIFMD Qualified Investor Standards",
        "jurisdiction": "EU",
        "tier_required": 3,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "AIFMD Article 43, MiFID II",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "UK FCA Consumer Duty Suitability Rule",
        "jurisdiction": "UK",
        "tier_required": 1,
        "product_category": "fixed_income",
        "risk_level": "conservative",
        "regulatory_ref": "FCA COBS 9.2, Consumer Duty PS22/9",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "UK Structured Products Risk Assessment",
        "jurisdiction": "UK",
        "tier_required": 2,
        "product_category": "structured",
        "risk_level": "aggressive",
        "regulatory_ref": "FCA COBS 9.2, PRIIPs Regulation (UK)",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "UK High Net Worth Exemption Criteria",
        "jurisdiction": "UK",
        "tier_required": 3,
        "product_category": "alternatives",
        "risk_level": "aggressive",
        "regulatory_ref": "FCA COBS 4.12, Financial Promotions Order",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "suitability_rule",
        "title": "Moderate Risk Portfolio Construction Rule",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": "equity",
        "risk_level": "moderate",
        "regulatory_ref": "FINRA Rule 2111, SEC Regulation BI",
        "stale": False,
        "pii": False,
    },
    # 10 Compliance Memos (tiers 2-4, US/EU)
    {
        "doc_type": "compliance_memo",
        "title": "Q1 2025 Trade Surveillance Summary",
        "jurisdiction": "US",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "FINRA Rule 3110, SEC Rule 17a-4",
        "stale": False,
        "pii": True,
    },
    {
        "doc_type": "compliance_memo",
        "title": "Private Placement Distribution Compliance",
        "jurisdiction": "US",
        "tier_required": 3,
        "product_category": "alternatives",
        "risk_level": None,
        "regulatory_ref": "SEC Regulation D, FINRA Rule 5122",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "compliance_memo",
        "title": "Senior Investor Protection Procedures",
        "jurisdiction": "US",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "FINRA Rule 2165, SEC Regulation BI",
        "stale": True,
        "pii": True,
    },
    {
        "doc_type": "compliance_memo",
        "title": "Cross-Border Transaction Reporting Requirements",
        "jurisdiction": "US",
        "tier_required": 3,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "SEC Rule 15c3-5, FINRA Rule 6830",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "compliance_memo",
        "title": "Ultra HNW Client Documentation Standards",
        "jurisdiction": "US",
        "tier_required": 4,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "FINRA Rule 4512, SEC Regulation S-P",
        "stale": False,
        "pii": True,
    },
    {
        "doc_type": "compliance_memo",
        "title": "EU DORA Operational Resilience Update",
        "jurisdiction": "EU",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "EU DORA Regulation 2022/2554",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "compliance_memo",
        "title": "MiFID II Transaction Reporting Amendments",
        "jurisdiction": "EU",
        "tier_required": 3,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "MiFIR Article 26, ESMA RTS 25",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "compliance_memo",
        "title": "EU Sustainable Finance Disclosure Update",
        "jurisdiction": "EU",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "SFDR Article 6, EU Taxonomy Regulation",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "compliance_memo",
        "title": "Private Fund Advisory Fee Review",
        "jurisdiction": "EU",
        "tier_required": 4,
        "product_category": "alternatives",
        "risk_level": None,
        "regulatory_ref": "AIFMD Article 22, MiFID II Article 24",
        "stale": False,
        "pii": True,
    },
    {
        "doc_type": "compliance_memo",
        "title": "AML Enhanced Due Diligence Protocol",
        "jurisdiction": "US",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "BSA/AML, FINRA Rule 3310, FinCEN CDD Rule",
        "stale": False,
        "pii": True,
    },
    # 10 Jurisdiction Disclosures (tiers 1-2, US/EU/UK/APAC)
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "US Broker-Dealer Relationship Disclosure",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "SEC Regulation BI, FINRA Rule 2267",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "US ERISA Fiduciary Duty Notice",
        "jurisdiction": "US",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "ERISA Section 404, DOL Fiduciary Rule",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "EU MiFID II Cost and Charges Disclosure",
        "jurisdiction": "EU",
        "tier_required": 1,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "MiFID II Article 24(4), ESMA Guidelines",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "EU PRIIPs Key Information Document Template",
        "jurisdiction": "EU",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "PRIIPs Regulation (EU) 1286/2014",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "UK FCA Client Categorisation Notice",
        "jurisdiction": "UK",
        "tier_required": 1,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "FCA COBS 3.5, MiFID II (UK)",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "UK Consumer Duty Fair Value Assessment",
        "jurisdiction": "UK",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "FCA PS22/9 Consumer Duty, PRIN 2A",
        "stale": True,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "APAC Cross-Border Advisory Disclosure",
        "jurisdiction": "APAC",
        "tier_required": 1,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "MAS FAA Section 27, SFC Code of Conduct",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "APAC Professional Investor Declaration",
        "jurisdiction": "APAC",
        "tier_required": 2,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "SFC SFO Schedule 1, MAS SFA Section 4A",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "US Privacy and Data Protection Notice",
        "jurisdiction": "US",
        "tier_required": 1,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "SEC Regulation S-P, GLBA",
        "stale": False,
        "pii": False,
    },
    {
        "doc_type": "jurisdiction_disclosure",
        "title": "UK GDPR Investment Data Processing Notice",
        "jurisdiction": "UK",
        "tier_required": 1,
        "product_category": None,
        "risk_level": None,
        "regulatory_ref": "UK GDPR Article 13, DPA 2018",
        "stale": True,
        "pii": False,
    },
]

DOC_TYPE_TO_DIR = {
    "product_factsheet": "product_factsheets",
    "suitability_rule": "suitability_rules",
    "compliance_memo": "compliance_memos",
    "jurisdiction_disclosure": "jurisdiction_disclosures",
}


def get_generation_prompt(spec: dict[str, Any]) -> str:
    stale_instruction = ""
    if spec["stale"]:
        stale_instruction = (
            "Set last_reviewed_at to a date MORE than 12 months ago (e.g., 2023-06-15). "
            "This document should appear stale and in need of review."
        )
    else:
        stale_instruction = (
            "Set last_reviewed_at to a recent date within the last 6 months "
            "(2025-01-01 to 2025-05-01)."
        )

    pii_instruction = ""
    if spec["pii"]:
        pii_instruction = (
            "IMPORTANT: Embed realistic PII patterns in the content: "
            "include at least one fictional person name (e.g., 'Robert J. Williams'), "
            "one phone number (e.g., '(212) 555-0147'), "
            "and one account number (e.g., 'Account #4821-7734'). "
            "These will be used to test PII redaction."
        )

    product_category_line = ""
    if spec["product_category"]:
        product_category_line = f"Product category: {spec['product_category']}"

    risk_level_line = ""
    if spec["risk_level"]:
        risk_level_line = f"Risk level: {spec['risk_level']}"

    firm = "Meridian Wealth Partners"
    return f"""Generate a realistic financial document for a wealth management firm called "{firm}".

Document specifications:
- Title: {spec["title"]}
- Type: {spec["doc_type"]}
- Jurisdiction: {spec["jurisdiction"]}
- Tier required: {spec["tier_required"]} (1=associate, 2=advisor, 3=senior, 4=private_wealth)
- Regulatory reference: {spec["regulatory_ref"]}
{product_category_line}
{risk_level_line}
{stale_instruction}
{pii_instruction}

Requirements:
1. Write 300-800 words of realistic financial/compliance content
2. Use professional wealth management language
3. Reference the specific regulations mentioned above naturally in the text
4. Include specific numbers, percentages, and financial terms
5. Make it sound like a real internal firm document, not a template

Return ONLY valid JSON with this exact structure (no markdown, no code fences):
{{
  "title": "{spec["title"]}",
  "doc_type": "{spec["doc_type"]}",
  "jurisdiction": "{spec["jurisdiction"]}",
  "tier_required": {spec["tier_required"]},
  "regulatory_ref": "{spec["regulatory_ref"]}",
  "last_reviewed_at": "YYYY-MM-DD",
  "product_category": {json.dumps(spec["product_category"])},
  "risk_level": {json.dumps(spec["risk_level"])},
  "content": "The full document content here..."
}}"""


async def generate_document(
    client: anthropic.AsyncAnthropic,
    spec: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    prompt = get_generation_prompt(spec)
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text  # type: ignore[union-attr]

    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]

    doc = json.loads(text)
    log.info(
        "generated_document",
        index=index + 1,
        title=doc["title"],
        doc_type=doc["doc_type"],
        jurisdiction=doc["jurisdiction"],
        word_count=len(doc["content"].split()),
    )
    return doc  # type: ignore[no-any-return]


async def main() -> None:
    dry_run = "--dry-run" in sys.argv

    log.info(
        "corpus_generation_plan",
        total_documents=len(DOCUMENT_SPECS),
        product_factsheets=sum(1 for s in DOCUMENT_SPECS if s["doc_type"] == "product_factsheet"),
        suitability_rules=sum(1 for s in DOCUMENT_SPECS if s["doc_type"] == "suitability_rule"),
        compliance_memos=sum(1 for s in DOCUMENT_SPECS if s["doc_type"] == "compliance_memo"),
        jurisdiction_disclosures=sum(
            1 for s in DOCUMENT_SPECS if s["doc_type"] == "jurisdiction_disclosure"
        ),
        estimated_cost_usd="$0.50-$1.00",
    )

    if dry_run:
        log.info("dry_run_complete")
        return

    settings = Settings()
    if not settings.anthropic_api_key:
        log.error("ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    generated = 0
    errors = 0

    for i, spec in enumerate(DOCUMENT_SPECS):
        try:
            doc = await generate_document(client, spec, i)
            doc_type_dir = DOC_TYPE_TO_DIR[spec["doc_type"]]
            output_dir = CORPUS_DIR / doc_type_dir
            output_dir.mkdir(parents=True, exist_ok=True)

            filename = f"{i + 1:02d}_{spec['title'].lower().replace(' ', '_')[:50]}.json"
            output_path = output_dir / filename
            output_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            generated += 1
        except Exception as e:
            log.error("generation_failed", index=i + 1, title=spec["title"], error=str(e))
            errors += 1

        # Rate limiting: small delay between requests
        if i < len(DOCUMENT_SPECS) - 1:
            await asyncio.sleep(0.5)

    log.info("corpus_generation_complete", generated=generated, errors=errors)


if __name__ == "__main__":
    asyncio.run(main())
