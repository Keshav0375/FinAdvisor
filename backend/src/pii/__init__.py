from __future__ import annotations

from src.config import Settings
from src.pii.fallback import RegexRedactor
from src.pii.redactor import PIIRedactor


def create_redactor(settings: Settings) -> PIIRedactor | RegexRedactor:
    if settings.pii_mode == "dlp" and settings.gcp_project_id:
        return PIIRedactor(project_id=settings.gcp_project_id)
    return RegexRedactor()
