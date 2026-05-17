from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ToolCallEvent(BaseModel):
    tool_name: str
    tool_input: dict[str, object]
    tool_use_id: str


class CitationEvent(BaseModel):
    index: int
    source_title: str
    regulatory_ref: str | None
    last_reviewed_at: str


class StreamEvent(BaseModel):
    type: Literal["text", "tool_call", "tool_result", "citation", "error"]
    content: str | None = None
    tool_call: ToolCallEvent | None = None
    citation: CitationEvent | None = None
