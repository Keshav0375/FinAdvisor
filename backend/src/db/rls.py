from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import UserClaims


async def set_rls_context(session: AsyncSession, user: UserClaims) -> None:
    await session.execute(
        text("SET app.user_tier = :tier"),
        {"tier": str(user.tier_level)},
    )
    await session.execute(
        text("SET app.user_jurisdictions = :jurisdictions"),
        {"jurisdictions": ",".join(user.jurisdictions)},
    )
