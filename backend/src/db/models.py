from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    tier_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    regulatory_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reviewed_at: Mapped[date] = mapped_column(Date, nullable=False)
    product_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(default=None)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    tier_required: Mapped[int] = mapped_column(Integer, nullable=False)
    regulatory_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reviewed_at: Mapped[date] = mapped_column(Date, nullable=False)
    source_title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(default=None)

    document: Mapped[Document] = relationship(back_populates="chunks")


class SuitabilityRule(Base):
    __tablename__ = "suitability_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rule_name: Mapped[str] = mapped_column(Text, nullable=False)
    product_category: Mapped[str] = mapped_column(Text, nullable=False)
    client_risk_profile: Mapped[str] = mapped_column(Text, nullable=False)
    min_tier_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    regulatory_ref: Mapped[str] = mapped_column(Text, nullable=False)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    last_reviewed_at: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(default=None)
