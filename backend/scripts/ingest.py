from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import Settings
from src.pii.fallback import RegexRedactor
from src.pii.redactor import PIIRedactor
from src.retrieval.embeddings import VoyageEmbeddings

log = structlog.get_logger()

CHUNK_SIZE = 2048
CHUNK_OVERLAP = 200
CORPUS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "corpus"


def chunk_text(
    text_content: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    separators = ["\n\n", "\n", ". ", " "]
    return _recursive_split(text_content, separators, chunk_size, overlap)


def _recursive_split(
    text_content: str,
    separators: list[str],
    chunk_size: int,
    overlap: int,
) -> list[str]:
    if len(text_content) <= chunk_size:
        return [text_content] if text_content.strip() else []

    sep = separators[0] if separators else ""
    remaining_seps = separators[1:] if len(separators) > 1 else []

    if sep:
        parts = text_content.split(sep)
    else:
        parts = [
            text_content[i : i + chunk_size]
            for i in range(0, len(text_content), chunk_size - overlap)
        ]
        return [p for p in parts if p.strip()]

    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = current + sep + part if current else part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current.strip():
                chunks.append(current.strip())
            if len(part) > chunk_size and remaining_seps:
                sub_chunks = _recursive_split(part, remaining_seps, chunk_size, overlap)
                chunks.extend(sub_chunks)
                current = ""
            else:
                current = part

    if current.strip():
        chunks.append(current.strip())

    if overlap > 0 and len(chunks) > 1:
        merged: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            overlap_text = prev[-overlap:] if len(prev) > overlap else prev
            merged.append(overlap_text + chunks[i])
        chunks = merged

    return chunks


def load_corpus_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for json_file in sorted(CORPUS_DIR.rglob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            doc = json.load(f)
            doc["_source_file"] = str(json_file.name)
            docs.append(doc)
    return docs


async def ingest(
    settings: Settings,
    dry_run: bool = False,
    target: str = "default",
) -> None:
    docs = load_corpus_docs()
    log.info("corpus_loaded", doc_count=len(docs))

    if not docs:
        log.error("no_documents_found", corpus_dir=str(CORPUS_DIR))
        return

    redactor: RegexRedactor | PIIRedactor
    if settings.pii_mode == "dlp" and settings.gcp_project_id:
        redactor = PIIRedactor(project_id=settings.gcp_project_id)
    else:
        redactor = RegexRedactor()

    all_chunks: list[dict[str, Any]] = []
    doc_records: list[dict[str, Any]] = []

    for doc in docs:
        content = doc["content"]
        result = redactor.redact(content)
        redacted_content = result.redacted_text

        text_chunks = chunk_text(redacted_content)

        doc_id = uuid.uuid4()
        last_reviewed = date.fromisoformat(doc["last_reviewed_at"])

        doc_records.append(
            {
                "id": doc_id,
                "title": doc["title"],
                "doc_type": doc["doc_type"],
                "jurisdiction": doc["jurisdiction"],
                "tier_required": doc["tier_required"],
                "regulatory_ref": doc.get("regulatory_ref"),
                "last_reviewed_at": last_reviewed,
                "product_category": doc.get("product_category"),
                "risk_level": doc.get("risk_level"),
                "raw_content": content,
                "created_at": datetime.now(UTC),
            }
        )

        for idx, chunk_content in enumerate(text_chunks):
            all_chunks.append(
                {
                    "id": uuid.uuid4(),
                    "document_id": doc_id,
                    "chunk_index": idx,
                    "content": chunk_content,
                    "jurisdiction": doc["jurisdiction"],
                    "tier_required": doc["tier_required"],
                    "regulatory_ref": doc.get("regulatory_ref"),
                    "last_reviewed_at": last_reviewed,
                    "source_title": doc["title"],
                    "created_at": datetime.now(UTC),
                }
            )

    log.info(
        "chunking_complete",
        total_docs=len(doc_records),
        total_chunks=len(all_chunks),
        avg_chunks_per_doc=round(len(all_chunks) / len(doc_records), 1),
    )

    if dry_run:
        log.info(
            "dry_run_complete",
            would_embed=len(all_chunks),
            would_insert_docs=len(doc_records),
        )
        return

    embedding_client = VoyageEmbeddings(api_key=settings.voyage_api_key)
    chunk_texts = [c["content"] for c in all_chunks]

    estimated_tokens = sum(len(t.split()) * 1.3 for t in chunk_texts)
    estimated_cost = estimated_tokens / 1_000_000 * 0.06
    log.info(
        "embedding_cost_estimate",
        total_chunks=len(chunk_texts),
        estimated_tokens=int(estimated_tokens),
        estimated_cost_usd=f"${estimated_cost:.4f}",
    )

    log.info("embedding_start", batch_count=len(chunk_texts))
    embeddings = await embedding_client.embed_texts(chunk_texts)
    log.info("embedding_complete", vectors_returned=len(embeddings))

    for i, emb in enumerate(embeddings):
        all_chunks[i]["embedding"] = str(emb)

    db_url = settings.database_url
    if target == "test":
        db_url = db_url.replace("/finadvisor", "/finadvisor_test")

    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session, session.begin():
        for doc_rec in doc_records:
            await session.execute(
                text("""
                        INSERT INTO documents (id, title, doc_type, jurisdiction, tier_required,
                            regulatory_ref, last_reviewed_at, product_category, risk_level,
                            raw_content, created_at)
                        VALUES (:id, :title, :doc_type, :jurisdiction, :tier_required,
                            :regulatory_ref, :last_reviewed_at, :product_category, :risk_level,
                            :raw_content, :created_at)
                    """),
                doc_rec,
            )

        for chunk_rec in all_chunks:
            await session.execute(
                text("""
                        INSERT INTO chunks (id, document_id, chunk_index, content, embedding,
                            jurisdiction, tier_required, regulatory_ref, last_reviewed_at,
                            source_title, created_at)
                        VALUES (:id, :document_id, :chunk_index, :content, :embedding,
                            :jurisdiction, :tier_required, :regulatory_ref, :last_reviewed_at,
                            :source_title, :created_at)
                    """),
                chunk_rec,
            )

    log.info(
        "ingest_complete",
        documents_inserted=len(doc_records),
        chunks_inserted=len(all_chunks),
    )

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest corpus documents into PostgreSQL + pgvector",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk only, no API calls or DB writes",
    )
    parser.add_argument(
        "--target",
        choices=["default", "test"],
        default="default",
        help="DB target",
    )
    args = parser.parse_args()

    settings = Settings()

    if not args.dry_run and not settings.voyage_api_key:
        log.error("missing_voyage_api_key")
        sys.exit(1)

    asyncio.run(ingest(settings, dry_run=args.dry_run, target=args.target))


if __name__ == "__main__":
    main()
