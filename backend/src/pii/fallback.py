from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

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


PATTERNS: dict[str, re.Pattern[str]] = {
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "PHONE": re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "EMAIL": re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b"),
    "ACCOUNT": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{0,4}\b"),
}


class RegexRedactor:
    def redact(self, text: str) -> RedactionResult:
        findings: list[Finding] = []
        redacted = text

        for label, pattern in PATTERNS.items():
            replacement = f"[{label}_REDACTED]"
            matches = pattern.findall(redacted)
            for match in matches:
                findings.append(Finding(info_type=label, original=match, replacement=replacement))
            redacted = pattern.sub(replacement, redacted)

        log.info(
            "pii_redaction_complete",
            mode="regex",
            findings_count=len(findings),
        )
        return RedactionResult(redacted_text=redacted, findings=findings)
