from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import UserClaims


async def set_rls_context(session: AsyncSession, user: UserClaims) -> None:
    # finadvisor is a superuser and bypasses RLS; switch to the app role
    await session.execute(text("SET ROLE finadvisor_app"))
    # SET does not support parameterized queries in asyncpg; values are
    # validated by the UserClaims Pydantic model so literal interpolation is safe.
    tier = int(user.tier_level)
    jurisdictions = ",".join(user.jurisdictions)
    await session.execute(text(f"SET app.user_tier = '{tier}'"))
    await session.execute(text(f"SET app.user_jurisdictions = '{jurisdictions}'"))


async def reset_rls_context(session: AsyncSession) -> None:
    await session.execute(text("RESET ROLE"))
