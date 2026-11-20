from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import Settings


def build_engine(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        settings.database_dsn,
        echo=False,
        pool_size=settings.db_pool_size,
        max_overflow=10,
        connect_args={
            "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)}
        },
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
