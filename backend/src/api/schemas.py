from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class Citation(BaseModel):
    index: int
    source_title: str
    regulatory_ref: str | None
    last_reviewed_at: date
    chunk_preview: str
    is_stale: bool


class ChatResponse(BaseModel):
    content: str
    citations: list[Citation]
    compliance_flags: list[str]
    trace_id: str
