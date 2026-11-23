from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator, Generator, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts.ingest import chunk_text, load_corpus_docs
from src.auth.models import UserClaims
from src.config import Settings
from src.main import app
from src.pii.fallback import RegexRedactor

# Dummy, non-secret-looking values for every required Settings key. Real values never live in code.
TEST_ENV: dict[str, str] = {
    "DB_HOST": "db.test.invalid",
    "DB_NAME": "finadvisor_unit",
    "DB_USER": "unit_user",
    "DB_PASSWORD": "unit-db-pass",
    "DB_POOL_SIZE": "5",
    "DB_STATEMENT_TIMEOUT_MS": "30000",
    "ANTHROPIC_API_KEY": "unit-anthropic-key",
    "VOYAGE_API_KEY": "unit-voyage-key",
    "JWT_SECRET": "unit-jwt-secret-0123456789-abcdefghij",
}

SEED_CONTENT_PREFIX = "__TEST_SEED__"

MIGRATE_TIMEOUT_S = 120

BACKEND_DIR = Path(__file__).resolve().parent.parent

# With REQUIRE_SERVICES=1 (set in CI) a missing service fails the test instead of skipping it.
REQUIRE_SERVICES = os.environ.get("REQUIRE_SERVICES", "") == "1"

# Test-only: where the Kafka broker listens (the compose host listener). Not a Settings key yet.
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "")

# Tests marked `live` call a real external API (Voyage, Anthropic); they run only on opt-in.
RUN_LIVE_API_TESTS = os.environ.get("LIVE_API_TESTS", "") == "1"

# Query embedding and the fixed chunk embedding used when the corpus is loaded without Voyage.
CORPUS_EMBEDDING = str([0.1] * 1024)

DB_REQUIRED_KEYS = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")


def build_database_url(environ: Mapping[str, str]) -> URL | None:
    """Test database URL from the Settings env keys; None when a required key is missing."""
    if any(not environ.get(key, "").strip() for key in DB_REQUIRED_KEYS):
        return None
    return URL.create(
        "postgresql+asyncpg",
        username=environ["DB_USER"],
        password=environ["DB_PASSWORD"],
        host=environ["DB_HOST"],
        port=int(environ.get("DB_PORT") or Settings.model_fields["db_port"].default),
        database=environ["DB_NAME"],
    )


# Read at import time, before the autouse `settings_env` fixture replaces the Settings keys.
TEST_DATABASE_URL: URL | None = build_database_url(os.environ)


class ServiceUnavailableError(pytest.fail.Exception):
    """A required service is down. Raised in a fixture, reported as a failed test, not an error."""


def require_service(name: str, reachable: bool, detail: str) -> None:
    """Fail when services are required, skip (naming the service) otherwise."""
    if reachable:
        return
    message = f"{name} not reachable: {detail}"
    if REQUIRE_SERVICES:
        raise ServiceUnavailableError(message, pytrace=False)
    pytest.skip(message)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Report a service gate that fails in fixture setup as a failed test, not an error: a
    missing service is what the test is about, and pytest counts setup failures as errors."""
    report = yield
    if (
        report.when == "setup"
        and report.failed
        and call.excinfo is not None
        and isinstance(call.excinfo.value, ServiceUnavailableError)
    ):
        report.when = "call"
    return report


def partition_items(
    items: Sequence[pytest.Item], run_live: bool
) -> tuple[list[pytest.Item], list[pytest.Item]]:
    """Mark tests without `integration` as `unit`; split off `live` tests unless opted in."""
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if item.get_closest_marker("live") is not None and not run_live:
            deselected.append(item)
            continue
        if item.get_closest_marker("integration") is None:
            item.add_marker(pytest.mark.unit)
        selected.append(item)
    return selected, deselected


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    selected, deselected = partition_items(items, RUN_LIVE_API_TESTS)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


async def _probe_database(url: URL) -> str:
    """Empty string when the database answers, else why not. Only connection errors count as
    unreachable; anything else (for example a missing greenlet) is a broken setup and raises."""
    timeout_s = Settings.model_fields["db_connect_timeout_s"].default
    engine = create_async_engine(url, connect_args={"timeout": timeout_s})
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError, asyncpg.PostgresError) as exc:
        return f"{type(exc).__name__} at {url.host}:{url.port}"
    finally:
        await engine.dispose()
    return ""


@functools.cache
def database_unreachable_reason() -> str:
    """Why the test database cannot be used, or an empty string. Probed once per session."""
    if TEST_DATABASE_URL is None:
        return "set DB_HOST, DB_NAME, DB_USER and DB_PASSWORD to a running database"
    return asyncio.run(_probe_database(TEST_DATABASE_URL))


@pytest.fixture
def postgres_available() -> None:
    """Gate for tests that need the database: fail or skip, naming PostgreSQL, when it is down."""
    reason = database_unreachable_reason()
    require_service("PostgreSQL", not reason, reason)


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Give every test a valid, isolated Settings environment (no ambient or .env values)."""
    from src.config import Settings

    for field in Settings.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, str):
            monkeypatch.delenv(alias, raising=False)
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    return dict(TEST_ENV)


def _migrate_env(url: URL) -> dict[str, str]:
    """Environment for the migrate job: the test database in the Settings keys, dummy rest."""
    env = {k: v for k, v in os.environ.items() if k not in _settings_aliases()}
    env.update(TEST_ENV)
    env.update(
        DB_HOST=url.host or "",
        DB_PORT=str(url.port),
        DB_NAME=url.database or "",
        DB_USER=url.username or "",
        DB_PASSWORD=url.password or "",
    )
    return env


def _settings_aliases() -> set[str]:
    return {
        str(field.validation_alias)
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }


@pytest.fixture(scope="session")
def integration_schema() -> Iterator[URL]:
    """The Alembic baseline applied to the test database, as the compose `migrate` job does."""
    reason = database_unreachable_reason()
    require_service("PostgreSQL", not reason, reason)
    assert TEST_DATABASE_URL is not None
    result = subprocess.run(
        [sys.executable, "-m", "src.entrypoint", "jobs", "migrate"],
        cwd=BACKEND_DIR,
        env=_migrate_env(TEST_DATABASE_URL),
        capture_output=True,
        text=True,
        timeout=MIGRATE_TIMEOUT_S,
    )
    if result.returncode != 0:
        pytest.fail(f"migrate job failed ({result.returncode}): {result.stdout}{result.stderr}")
    yield TEST_DATABASE_URL


@pytest.fixture
async def async_engine(integration_schema: URL):  # type: ignore[no-untyped-def]
    engine = create_async_engine(integration_schema)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:  # type: ignore[no-untyped-def]
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


async def _reset_session(session: AsyncSession) -> None:
    """Leave the role a test set (RLS tests SET ROLE) and drop any failed transaction."""
    with contextlib.suppress(Exception):
        await session.rollback()
    await session.execute(text("RESET ROLE"))


@pytest.fixture
async def corpus_session(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """The corpus as `scripts/ingest.py` stores it (same chunking and PII redaction), with a
    fixed embedding instead of Voyage vectors, so the tests need no API key. Removed afterwards."""
    redactor = RegexRedactor()
    document_ids: list[uuid.UUID] = []
    for doc in load_corpus_docs():
        doc_id = uuid.uuid4()
        document_ids.append(doc_id)
        reviewed = date.fromisoformat(doc["last_reviewed_at"])
        await db_session.execute(
            text("""
                INSERT INTO documents (id, title, doc_type, jurisdiction, tier_required,
                    regulatory_ref, last_reviewed_at, product_category, risk_level,
                    raw_content, created_at)
                VALUES (:id, :title, :doc_type, :jurisdiction, :tier_required,
                    :regulatory_ref, :last_reviewed_at, :product_category, :risk_level,
                    :raw_content, :created_at)
            """),
            {
                "id": doc_id,
                "title": doc["title"],
                "doc_type": doc["doc_type"],
                "jurisdiction": doc["jurisdiction"],
                "tier_required": doc["tier_required"],
                "regulatory_ref": doc.get("regulatory_ref"),
                "last_reviewed_at": reviewed,
                "product_category": doc.get("product_category"),
                "risk_level": doc.get("risk_level"),
                "raw_content": doc["content"],
                "created_at": datetime.now(UTC),
            },
        )
        for index, content in enumerate(chunk_text(redactor.redact(doc["content"]).redacted_text)):
            await db_session.execute(
                text("""
                    INSERT INTO chunks (document_id, chunk_index, content, embedding,
                        jurisdiction, tier_required, regulatory_ref, last_reviewed_at,
                        source_title)
                    VALUES (:document_id, :chunk_index, :content, :embedding,
                        :jurisdiction, :tier_required, :regulatory_ref, :last_reviewed_at,
                        :source_title)
                """),
                {
                    "document_id": doc_id,
                    "chunk_index": index,
                    "content": content,
                    "embedding": CORPUS_EMBEDDING,
                    "jurisdiction": doc["jurisdiction"],
                    "tier_required": doc["tier_required"],
                    "regulatory_ref": doc.get("regulatory_ref"),
                    "last_reviewed_at": reviewed,
                    "source_title": doc["title"],
                },
            )
    await db_session.commit()

    yield db_session

    await _reset_session(db_session)
    await db_session.execute(
        text("DELETE FROM documents WHERE id = ANY(:ids)"), {"ids": document_ids}
    )
    await db_session.commit()


@pytest.fixture
async def seeded_session(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncSession, None]:
    """Eleven marked documents and chunks, one per tier and jurisdiction. The schema already has
    RLS on `chunks` (the Alembic baseline), so only rows are seeded."""
    test_doc_ids: list[str] = []

    chunks_data = [
        (f"{SEED_CONTENT_PREFIX} US tier-1 chunk", "US", 1, "US Product Sheet"),
        (f"{SEED_CONTENT_PREFIX} US tier-2 chunk", "US", 2, "US Senior Guide"),
        (f"{SEED_CONTENT_PREFIX} US tier-3 chunk", "US", 3, "US Private Strategy"),
        (f"{SEED_CONTENT_PREFIX} US tier-4 chunk", "US", 4, "US Ultra HNW Doc"),
        (f"{SEED_CONTENT_PREFIX} EU tier-1 chunk", "EU", 1, "EU Basic Disclosure"),
        (f"{SEED_CONTENT_PREFIX} EU tier-2 chunk", "EU", 2, "EU Advisory Guide"),
        (f"{SEED_CONTENT_PREFIX} EU tier-3 chunk", "EU", 3, "EU Compliance Memo"),
        (f"{SEED_CONTENT_PREFIX} UK tier-1 chunk", "UK", 1, "UK Regulatory Notice"),
        (f"{SEED_CONTENT_PREFIX} UK tier-2 chunk", "UK", 2, "UK Advisory Guide"),
        (f"{SEED_CONTENT_PREFIX} UK tier-3 chunk", "UK", 3, "UK Compliance Memo"),
        (f"{SEED_CONTENT_PREFIX} UK tier-4 chunk", "UK", 4, "UK Private Wealth"),
    ]

    for content, jurisdiction, tier, title in chunks_data:
        doc_id = uuid.uuid4()
        test_doc_ids.append(str(doc_id))
        await db_session.execute(
            text("""
                INSERT INTO documents (id, title, doc_type, jurisdiction, tier_required,
                    last_reviewed_at, raw_content)
                VALUES (:id, :title, 'product_factsheet', :jurisdiction, :tier,
                    :reviewed, :content)
            """),
            {
                "id": str(doc_id),
                "title": title,
                "jurisdiction": jurisdiction,
                "tier": tier,
                "reviewed": date(2025, 1, 15),
                "content": content,
            },
        )
        await db_session.execute(
            text("""
                INSERT INTO chunks (document_id, chunk_index, content, embedding,
                    jurisdiction, tier_required, last_reviewed_at, source_title)
                VALUES (:doc_id, 0, :content, :embedding,
                    :jurisdiction, :tier, :reviewed, :title)
            """),
            {
                "doc_id": str(doc_id),
                "content": content,
                "embedding": CORPUS_EMBEDDING,
                "jurisdiction": jurisdiction,
                "tier": tier,
                "reviewed": date(2025, 1, 15),
                "title": title,
            },
        )
    await db_session.commit()

    yield db_session

    await _reset_session(db_session)
    for doc_id in test_doc_ids:
        await db_session.execute(text("DELETE FROM chunks WHERE document_id = :id"), {"id": doc_id})
        await db_session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
    await db_session.commit()


SARAH_CHEN = UserClaims(
    sub="sarah_chen",
    name="Sarah Chen",
    tier="senior",
    tier_level=3,
    jurisdictions=["US"],
    licenses=["Series-7", "Series-66"],
)

ALEX_KIM = UserClaims(
    sub="alex_kim",
    name="Alex Kim",
    tier="associate",
    tier_level=1,
    jurisdictions=["EU"],
    licenses=["MiFID-II"],
)

JAMES_WRIGHT = UserClaims(
    sub="james_wright",
    name="James Wright",
    tier="private_wealth",
    tier_level=4,
    jurisdictions=["UK"],
    licenses=["FCA"],
)

PRIYA_SHARMA = UserClaims(
    sub="priya_sharma",
    name="Priya Sharma",
    tier="advisor",
    tier_level=2,
    jurisdictions=["US", "EU"],
    licenses=["Series-7", "MiFID-II"],
)


@pytest.fixture
async def test_client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def override_dependencies():  # type: ignore[no-untyped-def]
    yield app.dependency_overrides
    app.dependency_overrides.clear()
