from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from google.cloud import dlp_v2

log = structlog.get_logger()


@dataclass
class Finding:
    info_type: str
    original: str
    replacement: str


@dataclass
class RedactionResult:
    redacted_text: str
    findings: list[Finding] = field(default_factory=list)


class PIIRedactor:
    INFO_TYPES = [
        "PERSON_NAME",
        "PHONE_NUMBER",
        "EMAIL_ADDRESS",
        "US_SOCIAL_SECURITY_NUMBER",
        "CANADA_SOCIAL_INSURANCE_NUMBER",
        "FINANCIAL_ACCOUNT_NUMBER",
        "CREDIT_CARD_NUMBER",
        "STREET_ADDRESS",
    ]

    def __init__(self, project_id: str) -> None:
        self.client = dlp_v2.DlpServiceClient()
        self.project_id = project_id
        self._parent = f"projects/{project_id}/locations/global"

    def redact(self, text: str) -> RedactionResult:
        inspect_config: dict[str, Any] = {
            "info_types": [{"name": t} for t in self.INFO_TYPES],
            "min_likelihood": "LIKELY",
        }
        deidentify_config: dict[str, Any] = {
            "info_type_transformations": {
                "transformations": [
                    {"primitive_transformation": {"replace_with_info_type_config": {}}}
                ]
            }
        }

        response = self.client.deidentify_content(
            request={
                "parent": self._parent,
                "inspect_config": inspect_config,
                "deidentify_config": deidentify_config,
                "item": {"value": text},
            }
        )

        findings = self._extract_findings(response)
        log.info(
            "pii_redaction_complete",
            mode="dlp",
            findings_count=len(findings),
        )
        return RedactionResult(redacted_text=response.item.value, findings=findings)

    def _extract_findings(self, response: Any) -> list[Finding]:
        findings: list[Finding] = []
        overview = response.overview
        if overview and overview.transformation_summaries:
            for summary in overview.transformation_summaries:
                info_type = summary.info_type.name if summary.info_type else "UNKNOWN"
                findings.append(
                    Finding(
                        info_type=info_type,
                        original="",
                        replacement=f"[{info_type}]",
                    )
                )
        return findings
