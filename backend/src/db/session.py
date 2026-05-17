from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import Settings


def build_engine(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
