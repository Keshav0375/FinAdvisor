from __future__ import annotations

from pydantic import BaseModel


class UserClaims(BaseModel):
    sub: str
    name: str
    tier: str
    tier_level: int
    jurisdictions: list[str]
    licenses: list[str]
