"""Integration tests for the Alembic baseline against a real Postgres.

Run against pgvector/pgvector:0.8.5-pg16 (what Cloud SQL and CI pin), for example:
    docker run -d --name fa-migrate-test -e POSTGRES_PASSWORD=postgres \
        -p 5433:5432 pgvector/pgvector:0.8.5-pg16
    export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/postgres
    pytest tests/test_migrations.py
Each test builds its own throwaway databases on that server and drops them afterwards.
Skipped when TEST_DATABASE_URL is unset or unreachable, or when psql/pg_dump are missing.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import Settings
from tests.conftest import TEST_DATABASE_URL, TEST_ENV, requires_db

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCHEMA_SQL = BACKEND_DIR / "db" / "schema.sql"
CLI_TIMEOUT_S = 120

pytestmark = [
    requires_db,
    pytest.mark.skipif(
        shutil.which("psql") is None or shutil.which("pg_dump") is None,
        reason="psql and pg_dump are required for the schema equality tests",
    ),
]


def _admin_url() -> URL:
    return make_url(TEST_DATABASE_URL)


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


async def _admin_execute(statement: str) -> None:
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _scalars(database: str, statement: str) -> list[Any]:
    engine = create_async_engine(_admin_url().set(database=database))
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(statement))).scalars())
    finally:
        await engine.dispose()


def _scalars_sync(database: str, statement: str) -> list[Any]:
    result: list[Any] = _run_async(_scalars(database, statement))
    return result


@pytest.fixture
def new_database() -> Iterator[Callable[[], str]]:
    created: list[str] = []

    def create() -> str:
        name = f"fa_t9_{uuid.uuid4().hex[:12]}"
        _run_async(_admin_execute(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return name

    yield create
    for name in created:
        _run_async(_admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _pg_env(database: str) -> dict[str, str]:
    url = _admin_url()
    return {
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password or "",
        "PGDATABASE": database,
    }


def _settings_env(database: str) -> dict[str, str]:
    """Environment for a child process that runs the migrations against `database`."""
    url = _admin_url()
    env = {k: v for k, v in os.environ.items() if k not in _settings_aliases()}
    env.update(TEST_ENV)
    env.update(
        DB_HOST=url.host or "localhost",
        DB_PORT=str(url.port or 5432),
        DB_USER=url.username or "",
        DB_PASSWORD=url.password or "",
        DB_NAME=database,
    )
    return env


def _settings_aliases() -> set[str]:
    return {
        str(field.validation_alias)
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=CLI_TIMEOUT_S
    )


def _migrate(database: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [sys.executable, "-m", "src.entrypoint", "jobs", "migrate"], _settings_env(database)
    )


def _dump(database: str) -> str:
    """Schema-only dump without the alembic bookkeeping table or per-run `\\restrict` tokens."""
    result = subprocess.run(
        [
            "pg_dump",
            "--schema-only",
            "--no-owner",
            "--no-privileges",
            "--exclude-table=alembic_version",
        ],
        env={**os.environ, **_pg_env(database)},
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_S,
        check=True,
    )
    return re.sub(r"^\\(un)?restrict .*\n", "", result.stdout, flags=re.MULTILINE)


def test_migrate_on_empty_database_reaches_0001_and_is_idempotent(
    new_database: Callable[[], str],
) -> None:
    database = new_database()
    first = _migrate(database)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _scalars_sync(database, "SELECT version_num FROM alembic_version") == ["0001"]
    dump_after_first = _dump(database)

    second = _migrate(database)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _scalars_sync(database, "SELECT version_num FROM alembic_version") == ["0001"]
    assert _dump(database) == dump_after_first


def test_migrated_schema_is_identical_to_schema_sql(new_database: Callable[[], str]) -> None:
    database_a = new_database()
    loaded = _run(
        ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(SCHEMA_SQL)],
        {**os.environ, **_pg_env(database_a)},
    )
    assert loaded.returncode == 0, loaded.stderr
    database_b = new_database()
    migrated = _migrate(database_b)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr

    dump_a, dump_b = _dump(database_a), _dump(database_b)
    assert "CREATE TABLE public.chunks" in dump_a
    assert dump_a == dump_b


def _load_schema_sql(database: str) -> None:
    loaded = _run(
        ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(SCHEMA_SQL)],
        {**os.environ, **_pg_env(database)},
    )
    assert loaded.returncode == 0, loaded.stderr


def _tables(database: str) -> list[str]:
    tables: list[str] = _scalars_sync(
        database, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
    )
    return tables


def test_migrate_refuses_a_schema_sql_database_and_never_stamps(
    new_database: Callable[[], str],
) -> None:
    database = new_database()
    _load_schema_sql(database)
    tables_before = _tables(database)

    refused = _migrate(database)
    output = refused.stdout + refused.stderr
    assert refused.returncode == 1, output
    assert "migrate_refused" in output
    assert "schema.sql" in output
    assert "python -m alembic stamp 0001" in output
    assert _tables(database) == tables_before
    assert "alembic_version" not in tables_before


def test_stamp_then_migrate_adopts_a_schema_sql_database(
    new_database: Callable[[], str],
) -> None:
    database = new_database()
    _load_schema_sql(database)
    dump_before = _dump(database)

    stamped = _run([sys.executable, "-m", "alembic", "stamp", "0001"], _settings_env(database))
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr

    migrated = _migrate(database)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    assert _scalars_sync(database, "SELECT version_num FROM alembic_version") == ["0001"]
    assert _dump(database) == dump_before


def test_downgrade_to_base_leaves_only_alembic_version(new_database: Callable[[], str]) -> None:
    database = new_database()
    migrated = _migrate(database)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    tables_sql = "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
    assert "chunks" in _scalars_sync(database, tables_sql)

    downgraded = _run(
        [sys.executable, "-m", "alembic", "downgrade", "base"], _settings_env(database)
    )
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
    assert _scalars_sync(database, tables_sql) == ["alembic_version"]
    roles = _scalars_sync(
        database, "SELECT rolname FROM pg_roles WHERE rolname LIKE 'finadvisor_%'"
    )
    assert sorted(roles) == ["finadvisor_app", "finadvisor_service"]
    extensions = _scalars_sync(
        database, "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pgcrypto')"
    )
    assert sorted(extensions) == ["pgcrypto", "vector"]
