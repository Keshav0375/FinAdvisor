from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agent.tools.escalate import (
    ESCALATE_TOOL,
    EscalateInput,
    EscalationRecord,
    escalate_to_compliance,
)

from .conftest import ALEX_KIM, SARAH_CHEN


@pytest.mark.asyncio
async def test_escalation_returns_record() -> None:
    input_data = EscalateInput(
        reason="Client requesting structured products outside advisor license",
        product_class="structured_products",
        advisor_licenses=["Series-7", "Series-66"],
    )
    result = await escalate_to_compliance(input_data, SARAH_CHEN)

    assert isinstance(result, EscalationRecord)
    assert result.advisor_id == "sarah_chen"
    assert result.advisor_name == "Sarah Chen"
    assert result.reason == input_data.reason
    assert result.product_class == "structured_products"
    assert result.advisor_licenses == ["Series-7", "Series-66"]
    assert result.advisor_jurisdictions == ["US"]
    assert result.advisor_tier == "senior"
    assert result.status == "pending_review"


@pytest.mark.asyncio
async def test_escalation_id_format() -> None:
    input_data = EscalateInput(
        reason="Regulatory conflict",
        product_class="derivatives",
        advisor_licenses=["MiFID-II"],
    )
    result = await escalate_to_compliance(input_data, ALEX_KIM)

    assert result.escalation_id.startswith("ESC-alex_kim-")


@pytest.mark.asyncio
async def test_escalation_timestamp_is_iso() -> None:
    input_data = EscalateInput(
        reason="Ambiguous suitability",
        product_class="alternatives",
        advisor_licenses=["Series-7"],
    )
    result = await escalate_to_compliance(input_data, SARAH_CHEN)

    assert "T" in result.timestamp
    assert "+" in result.timestamp or result.timestamp.endswith("Z") or "+00:00" in result.timestamp


@pytest.mark.asyncio
async def test_escalation_logs_warning() -> None:
    input_data = EscalateInput(
        reason="License mismatch",
        product_class="insurance",
        advisor_licenses=["Series-7"],
    )

    with patch("src.agent.tools.escalate.log") as mock_log:
        result = await escalate_to_compliance(input_data, SARAH_CHEN)

        mock_log.warning.assert_called_once_with(
            "compliance_escalation",
            escalation_id=result.escalation_id,
            advisor_id="sarah_chen",
            reason="License mismatch",
            product_class="insurance",
            advisor_licenses=["Series-7"],
        )


@pytest.mark.asyncio
async def test_escalate_tool_schema() -> None:
    assert ESCALATE_TOOL["name"] == "escalate_to_compliance"
    assert "input_schema" in ESCALATE_TOOL
    schema = ESCALATE_TOOL["input_schema"]
    assert "reason" in schema["properties"]
    assert "product_class" in schema["properties"]
    assert "advisor_licenses" in schema["properties"]


@pytest.mark.asyncio
async def test_different_users_produce_different_records() -> None:
    input_data = EscalateInput(
        reason="Cross-border product",
        product_class="forex",
        advisor_licenses=["Series-7"],
    )
    sarah_result = await escalate_to_compliance(input_data, SARAH_CHEN)
    alex_result = await escalate_to_compliance(input_data, ALEX_KIM)

    assert sarah_result.advisor_id != alex_result.advisor_id
    assert sarah_result.advisor_jurisdictions == ["US"]
    assert alex_result.advisor_jurisdictions == ["EU"]
    assert sarah_result.advisor_tier == "senior"
    assert alex_result.advisor_tier == "associate"
