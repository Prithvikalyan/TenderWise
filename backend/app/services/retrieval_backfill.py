"""Build retrieval chunks and optionally precompute pgvector embeddings.

Run from ``backend``:
    python -m app.services.retrieval_backfill
    python -m app.services.retrieval_backfill --tender-id <UUID> --embed
"""

from __future__ import annotations

import argparse
import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_engine
from app.models.retrieval import DocumentRetrievalChunkEmbedding
from app.models.tender import Tender
from app.services.ai import generate_embeddings
from app.services.advanced_retrieval import _load_current_chunks, _valid_vector

logger = logging.getLogger("tenderwise.retrieval_backfill")


def run_backfill(session: Session, *, tender_id: UUID | None, embed: bool, batch_size: int) -> dict[str, int]:
    settings = get_settings()
    if session.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Retrieval backfill requires PostgreSQL with the pgvector extension")
    ext = session.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).first()
    if not ext:
        raise RuntimeError("pgvector is not installed in this database. Install the PostgreSQL vector extension and run migrations first.")
    if embed and not settings.nvidia_api_key:
        raise RuntimeError("NVIDIA_API_KEY is required when --embed is specified")

    tender_ids = [tender_id] if tender_id else list(session.scalars(select(Tender.id)).all())
    totals = {"tenders": 0, "chunks": 0, "embedded": 0, "already_embedded": 0}
    model = settings.nvidia_embedding_model

    for current_tender_id in tender_ids:
        chunks, _run_item_ids = _load_current_chunks(session, current_tender_id, settings)
        totals["tenders"] += 1
        totals["chunks"] += len(chunks)
        logger.info("Tender %s has %s current retrieval chunks", current_tender_id, len(chunks))
        if not embed or not chunks:
            continue
        rows = session.scalars(select(DocumentRetrievalChunkEmbedding).where(
            DocumentRetrievalChunkEmbedding.embedding_model == model,
            DocumentRetrievalChunkEmbedding.chunk_id.in_([chunk.id for chunk in chunks]),
        )).all()
        cache = {row.chunk_id: row for row in rows}
        missing = []
        for chunk in chunks:
            cached = cache.get(chunk.id)
            if cached and cached.content_sha256 == chunk.content_sha256 and _valid_vector(cached.embedding):
                totals["already_embedded"] += 1
            else:
                missing.append(chunk)
        for offset in range(0, len(missing), batch_size):
            batch = missing[offset:offset + batch_size]
            vectors = generate_embeddings(
                [chunk.content_text[:6000] for chunk in batch],
                input_type="passage",
                model=model,
            )
            if len(vectors) != len(batch) or any(not _valid_vector(vector) for vector in vectors):
                raise RuntimeError("Embedding provider returned an invalid vector batch")
            for chunk, vector in zip(batch, vectors):
                cached = cache.get(chunk.id)
                if cached is None:
                    session.add(DocumentRetrievalChunkEmbedding(
                        chunk_id=chunk.id,
                        embedding_model=model,
                        content_sha256=chunk.content_sha256,
                        embedding=[float(value) for value in vector],
                    ))
                else:
                    cached.content_sha256 = chunk.content_sha256
                    cached.embedding = [float(value) for value in vector]
            session.commit()
            totals["embedded"] += len(batch)
            logger.info("Embedded %s of %s missing chunks for tender %s", min(offset + len(batch), len(missing)), len(missing), current_tender_id)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill TenderWise RAG chunks and optional pgvector embeddings")
    parser.add_argument("--tender-id", type=UUID, help="Limit processing to one tender UUID")
    parser.add_argument("--embed", action="store_true", help="Generate and store embeddings for missing chunks")
    parser.add_argument("--batch-size", type=int, default=64, choices=range(1, 129), metavar="1..128")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with Session(get_engine()) as session:
            totals = run_backfill(session, tender_id=args.tender_id, embed=args.embed, batch_size=args.batch_size)
        print("Retrieval backfill complete:", totals)
        return 0
    except Exception as exc:
        logger.error("Retrieval backfill failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
