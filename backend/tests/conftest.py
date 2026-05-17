from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.models import UserClaims
from src.db.models import Base

TEST_DATABASE_URL = "postgresql+asyncpg://finadvisor:localdev@localhost:5432/finadvisor"

SEED_CONTENT_PREFIX = "__TEST_SEED__"


def can_connect_to_db() -> bool:
    import asyncio

    async def _check() -> bool:
        try:
            engine = create_async_engine(TEST_DATABASE_URL)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
        except Exception:
            return False
        return True

    return asyncio.run(_check())


requires_db = pytest.mark.skipif(
    not can_connect_to_db(),
    reason="PostgreSQL not available at localhost:5432",
)


@pytest.fixture
async def async_engine():  # type: ignore[no-untyped-def]
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:  # type: ignore[no-untyped-def]
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
async def live_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def seeded_session(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncSession, None]:
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
        embedding = [0.1] * 1024
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
                "embedding": str(embedding),
                "jurisdiction": jurisdiction,
                "tier": tier,
                "reviewed": date(2025, 1, 15),
                "title": title,
            },
        )
    await db_session.commit()

    await db_session.execute(text("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY"))
    await db_session.execute(text("ALTER TABLE chunks FORCE ROW LEVEL SECURITY"))
    await db_session.execute(
        text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies WHERE tablename = 'chunks'
                    AND policyname = 'chunk_visibility'
                ) THEN
                    CREATE POLICY chunk_visibility ON chunks FOR SELECT USING (
                        tier_required <= current_setting('app.user_tier')::int
                        AND jurisdiction = ANY(
                            string_to_array(current_setting('app.user_jurisdictions'), ',')
                        )
                    );
                END IF;
            END
            $$
        """)
    )
    await db_session.commit()

    yield db_session

    with contextlib.suppress(Exception):
        await db_session.rollback()
    await db_session.execute(text("RESET ROLE"))
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
