from __future__ import annotations

from fastapi import APIRouter, Depends

from src.auth.jwt import get_current_user
from src.auth.models import UserClaims

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/me")
async def me(user: UserClaims = Depends(get_current_user)) -> dict[str, object]:
    return user.model_dump()
