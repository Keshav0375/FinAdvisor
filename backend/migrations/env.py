from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import inspect, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import Settings
from src.db.migration_guard import REFUSE_UNVERSIONED_ATTR, check_versioned

# Migrations are hand-written SQL, so there is no model metadata to autogenerate from.
target_metadata = None


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    # The check runs inside Alembic's transaction: inspecting before it would autobegin a
    # transaction that Alembic then treats as the caller's and never commits.
    with context.begin_transaction():
        if context.config.attributes.get(REFUSE_UNVERSIONED_ATTR):
            check_versioned(inspect(connection).get_table_names(schema="public"))
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
