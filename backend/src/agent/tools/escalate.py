from __future__ import annotations

from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field

from src.auth.models import UserClaims

log = structlog.get_logger()


class EscalateInput(BaseModel):
    reason: str = Field(description="Why this query needs compliance review")
    product_class: str = Field(description="The restricted product class involved")
    advisor_licenses: list[str] = Field(description="Advisor's current licenses")


class EscalationRecord(BaseModel):
    escalation_id: str
    advisor_id: str
    advisor_name: str
    reason: str
    product_class: str
    advisor_licenses: list[str]
    advisor_jurisdictions: list[str]
    advisor_tier: str
    timestamp: str
    status: str


ESCALATE_TOOL = {
    "name": "escalate_to_compliance",
    "description": (
        "Flag a query for compliance department review. Use when: "
        "(1) query involves a product class outside advisor's licenses, "
        "(2) regulatory conflict detected, "
        "(3) suitability determination is ambiguous and requires human review."
    ),
    "input_schema": EscalateInput.model_json_schema(),
}


async def escalate_to_compliance(
    input_data: EscalateInput,
    user: UserClaims,
) -> EscalationRecord:
    timestamp = datetime.now(UTC).isoformat()
    escalation_id = f"ESC-{user.sub}-{int(datetime.now(UTC).timestamp())}"

    record = EscalationRecord(
        escalation_id=escalation_id,
        advisor_id=user.sub,
        advisor_name=user.name,
        reason=input_data.reason,
        product_class=input_data.product_class,
        advisor_licenses=input_data.advisor_licenses,
        advisor_jurisdictions=user.jurisdictions,
        advisor_tier=user.tier,
        timestamp=timestamp,
        status="pending_review",
    )

    log.warning(
        "compliance_escalation",
        escalation_id=record.escalation_id,
        advisor_id=record.advisor_id,
        reason=record.reason,
        product_class=record.product_class,
        advisor_licenses=record.advisor_licenses,
    )

    return record
