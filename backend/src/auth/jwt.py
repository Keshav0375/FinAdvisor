from __future__ import annotations

from fastapi import HTTPException, Request

from src.auth.mock_users import MOCK_USERS
from src.auth.models import UserClaims


async def get_current_user(request: Request) -> UserClaims:
    user_id = request.headers.get("X-User-Id")
    if not user_id or user_id not in MOCK_USERS:
        raise HTTPException(status_code=401, detail="Unknown user")
    return UserClaims(**MOCK_USERS[user_id])
