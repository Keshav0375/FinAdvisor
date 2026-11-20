from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import Settings

# Migrations are hand-written SQL, so there is no model metadata to autogenerate from.
target_metadata = None


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    settings = Settings()
    # The DSN holds the password, so it goes straight to the engine and never into alembic.ini.
    engine = create_async_engine(
        settings.database_dsn,
        poolclass=pool.NullPool,
        connect_args={"timeout": settings.db_connect_timeout_s},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported; run against a database")
run_migrations_online()
