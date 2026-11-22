"""Tender-scoped hybrid RAG retrieval using BM25, pgvector, RRF and reranking."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.vector_types import _PostgresHalfVector
from app.models.document_processing import (
    DocumentExtractionUnit, DocumentProcessingRun, DocumentProcessingRunItem,
)
from app.models.retrieval import (
    DocumentRetrievalChunk, DocumentRetrievalChunkEmbedding, EMBEDDING_DIMENSIONS,
)
from app.models.tender_document import TenderDocument
from app.services.ai import (
    AIServiceError, EmbeddingServiceError, generate_embeddings, rerank_passages,
)
from app.services.retrieval_chunks import CHUNKER_VERSION, build_retrieval_chunks

logger = logging.getLogger(__name__)

MAX_UNITS_TO_SCAN = 3000
MAX_EVIDENCE_SOURCES = 8
MAX_SOURCE_CHARS = 2200
MAX_TOTAL_CONTEXT_CHARS = 12000
MAX_EMBEDDING_BATCH_SIZE = 64
MAX_EMBEDDING_TEXT_CHARS = 6000
STOP_WORDS = {
    "the", "and", "for", "are", "was", "were", "with", "from", "that", "this",
    "what", "when", "where", "which", "who", "how", "does", "do", "is", "in",
    "on", "at", "to", "of", "a", "an", "be", "by", "or", "as", "it", "its",
    "can", "could", "would", "should", "please", "tell", "me", "about", "all",
    "any", "under", "their", "they", "them", "must", "required", "requirement",
    "requirements",
}
TOPIC_GROUPS = {
    "eligibility": {"eligibility", "eligible", "bidder", "bidders", "qualification", "qualify", "registration", "registered", "business", "company", "legal", "blacklist", "blacklisted", "declaration", "experience", "contract", "similar", "turnover", "annual", "financial", "audited", "certificate", "purchase", "work", "order", "completion"},
    "experience": {"experience", "similar", "contract", "contracts", "completed", "financial", "years", "purchase", "work", "order", "completion", "certificate"},
    "turnover": {"turnover", "annual", "average", "financial", "years", "audited", "statements", "chartered", "accountant", "certificate", "revenue"},
    "registration": {"registration", "registered", "business", "company", "legal", "certificate", "gst", "pan"},
    "blacklisting": {"blacklist", "blacklisted", "declaration", "government", "department", "debarred", "non-blacklisting"},
    "emd": {"emd", "earnest", "money", "deposit", "amount", "banker", "cheque", "demand", "draft", "validity", "valid", "days", "favour", "payee"},
    "deadline": {"deadline", "submission", "submit", "date", "time", "closing", "opening", "bid", "bidding", "clarification"},
    "technical": {"technical", "specification", "display", "screen", "resolution", "processor", "memory", "ram", "storage", "warranty", "equipment", "installation"},
    "payment": {"payment", "invoice", "advance", "pay", "paid", "days", "acceptance", "delivery", "installation"},
    "warranty": {"warranty", "onsite", "on-site", "years", "repair", "replacement"},
}


@dataclass
class RetrievalTrace:
    chunks_scanned: int = 0
    lexical_candidates: int = 0
    dense_candidates: int = 0
    fused_candidates: int = 0
    selected_sources: int = 0
    embedding_coverage: float = 0.0
    retrieval_ms: int = 0
    rerank_ms: int = 0
    rerank_mode: str = "disabled"
    query_variant_count: int = 1
    query_strategy: str = "original_question"
    embedding_model: str = ""
    ranked_evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RetrievalResult:
    evidence: list[dict[str, Any]]
    retrieval_mode: str
    semantic_candidate_count: int
    trace: RetrievalTrace = field(default_factory=RetrievalTrace)

    def __iter__(self) -> Iterator[Any]:
        # Preserve the old private helper's three-value unpacking contract.
        yield self.evidence
        yield self.retrieval_mode
        yield self.semantic_candidate_count


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z0-9]+", value.lower()) if len(token) >= 2 and token not in STOP_WORDS}


def _detect_topics(question: str) -> list[str]:
    q = question.lower()
    topics: list[str] = []
    if any(term in q for term in ("eligibility", "eligible", "qualify", "qualification", "all requirements", "bidder requirements")):
        topics.extend(["eligibility", "registration", "experience", "turnover", "blacklisting"])
    phrases = {
        "experience": ("experience", "similar contract", "similar work", "past contract"),
        "turnover": ("turnover", "annual revenue", "financial capacity"),
        "registration": ("registration", "registered business", "company registration"),
        "blacklisting": ("blacklist", "blacklisted", "debarred", "non-blacklisting"),
        "emd": ("emd", "earnest money", "deposit"),
        "deadline": ("deadline", "submission date", "last date", "bid date", "opening date"),
        "technical": ("technical", "specification", "minimum specification"),
        "payment": ("payment", "invoice", "advance payment"),
        "warranty": ("warranty",),
    }
    for topic, values in phrases.items():
        if any(value in q for value in values):
            topics.append(topic)
    return list(dict.fromkeys(topics))


def _query_variants(question: str, topics: list[str]) -> list[str]:
    variants = [question]
    for topic in topics:
        words = sorted(TOPIC_GROUPS.get(topic, set()))
        variants.append(f"{topic} tender clauses requirements: {' '.join(words)}")
    # A broad eligibility question can otherwise generate too many very similar
    # subqueries. Retain the original and the most useful focused dimensions.
    return list(dict.fromkeys(variants))[:5]


def _bm25_scores(query_tokens: set[str], documents: list[str]) -> list[float]:
    if not documents or not query_tokens:
        return [0.0] * len(documents)
    tokenized = [re.findall(r"[a-zA-Z0-9]+", value.lower()) for value in documents]
    lengths = [len(tokens) for tokens in tokenized]
    average_length = sum(lengths) / max(len(lengths), 1)
    frequencies = [Counter(tokens) for tokens in tokenized]
    document_frequency: Counter[str] = Counter()
    for counts in frequencies:
        document_frequency.update(counts.keys())
    result: list[float] = []
    k1, b, count = 1.5, 0.75, len(documents)
    for counts, length in zip(frequencies, lengths):
        score = 0.0
        for token in query_tokens:
            frequency = counts.get(token, 0)
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1.0 - b + b * length / max(average_length, 1.0))
            score += idf * frequency * (k1 + 1.0) / denominator
        result.append(score)
    return result


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _valid_vector(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == EMBEDDING_DIMENSIONS and all(
        not isinstance(part, bool) and isinstance(part, (int, float)) and math.isfinite(float(part))
        for part in value
    )


def _latest_run_item_ids(session: Session, tender_id: UUID) -> list[UUID]:
    """Return the latest *completed successful* processing item per tender document."""
    ranked = (
        select(
            DocumentProcessingRunItem.id.label("run_item_id"),
            func.row_number().over(
                partition_by=DocumentProcessingRunItem.document_id,
                order_by=(
                    DocumentProcessingRun.completed_at.desc().nulls_last(),
                    DocumentProcessingRun.created_at.desc(),
                    DocumentProcessingRunItem.completed_at.desc().nulls_last(),
                    DocumentProcessingRunItem.id.desc(),
                ),
            ).label("position"),
        )
        .join(DocumentProcessingRun, DocumentProcessingRun.id == DocumentProcessingRunItem.run_id)
        .where(
            DocumentProcessingRun.tender_id == tender_id,
            DocumentProcessingRunItem.status == "completed",
            DocumentProcessingRunItem.document_id.is_not(None),
        )
        .subquery()
    )
    return list(session.scalars(select(ranked.c.run_item_id).where(ranked.c.position == 1)).all())


def _load_current_chunks(session: Session, tender_id: UUID, settings) -> tuple[list[DocumentRetrievalChunk], list[UUID]]:
    current_run_item_ids = _latest_run_item_ids(session, tender_id)
    if not current_run_item_ids:
        return [], []

    chunk_query = select(DocumentRetrievalChunk).where(
        DocumentRetrievalChunk.tender_id == tender_id,
        DocumentRetrievalChunk.run_item_id.in_(current_run_item_ids),
        DocumentRetrievalChunk.chunker_version == CHUNKER_VERSION,
    )
    # Determine which run items already have chunks without materializing the
    # entire corpus. The actual retrieval scan is bounded at the SQL layer.
    chunked_items = set(session.scalars(
        select(DocumentRetrievalChunk.run_item_id)
        .where(
            DocumentRetrievalChunk.tender_id == tender_id,
            DocumentRetrievalChunk.run_item_id.in_(current_run_item_ids),
            DocumentRetrievalChunk.chunker_version == CHUNKER_VERSION,
        )
        .distinct()
    ).all())
    missing_item_ids = [item_id for item_id in current_run_item_ids if item_id not in chunked_items]
    if missing_item_ids:
        rows = session.execute(
            select(DocumentProcessingRunItem, TenderDocument.id, TenderDocument.original_filename)
            .join(TenderDocument, TenderDocument.id == DocumentProcessingRunItem.document_id)
            .where(DocumentProcessingRunItem.id.in_(missing_item_ids))
        ).all()
        for run_item, document_id, _filename in rows:
            units = list(session.scalars(
                select(DocumentExtractionUnit)
                .where(DocumentExtractionUnit.run_item_id == run_item.id)
                .order_by(DocumentExtractionUnit.unit_index.asc())
            ).all())
            new_chunks = build_retrieval_chunks(
                tender_id=tender_id,
                document_id=document_id,
                run_item_id=run_item.id,
                units=units,
                target_tokens=getattr(settings, "tenderwise_chunk_target_tokens", 600),
                overlap_tokens=getattr(settings, "tenderwise_chunk_overlap_tokens", 80),
            )
            session.add_all(new_chunks)
        if missing_item_ids:
            # Lazy backfill keeps existing installations usable immediately
            # after migration; the CLI command can prebuild a larger corpus.
            session.commit()
    chunks = list(session.scalars(
        chunk_query
        .order_by(DocumentRetrievalChunk.run_item_id, DocumentRetrievalChunk.chunk_index)
        .limit(MAX_UNITS_TO_SCAN)
    ).all())
    return chunks, current_run_item_ids


def _ensure_chunk_embeddings(
    session: Session,
    chunks: list[DocumentRetrievalChunk],
    model: str,
    question: str,
    lexical_order: list[DocumentRetrievalChunk],
    max_backfill: int,
) -> tuple[dict[UUID, list[float]], float]:
    if not chunks:
        return {}, 0.0
    chunk_ids = [chunk.id for chunk in chunks]
    cached_rows = session.scalars(select(DocumentRetrievalChunkEmbedding).where(
        DocumentRetrievalChunkEmbedding.embedding_model == model,
        DocumentRetrievalChunkEmbedding.chunk_id.in_(chunk_ids),
    )).all()
    cache = {row.chunk_id: row for row in cached_rows}
    by_id = {chunk.id: chunk for chunk in chunks}
    vectors: dict[UUID, list[float]] = {}
    pending: list[DocumentRetrievalChunk] = []
    for chunk in chunks:
        cached = cache.get(chunk.id)
        if cached and cached.content_sha256 == chunk.content_sha256 and _valid_vector(cached.embedding):
            vectors[chunk.id] = [float(value) for value in cached.embedding]
        else:
            pending.append(chunk)
    if max_backfill > 0 and pending:
        lexical_ids = [chunk.id for chunk in lexical_order]
        lexical_rank = {chunk_id: index for index, chunk_id in enumerate(lexical_ids)}
        pending.sort(key=lambda chunk: (lexical_rank.get(chunk.id, 10**9), chunk.chunk_index))
        pending = pending[:max_backfill]
        for offset in range(0, len(pending), MAX_EMBEDDING_BATCH_SIZE):
            batch = pending[offset:offset + MAX_EMBEDDING_BATCH_SIZE]
            contents = [chunk.content_text[:MAX_EMBEDDING_TEXT_CHARS] for chunk in batch]
            vectors_for_batch = generate_embeddings(contents, input_type="passage", model=model)
            if len(vectors_for_batch) != len(batch) or any(not _valid_vector(vector) for vector in vectors_for_batch):
                raise EmbeddingServiceError("Embedding provider returned an invalid 2048-dimensional passage batch.")
            for chunk, vector in zip(batch, vectors_for_batch):
                normalized = [float(value) for value in vector]
                vectors[chunk.id] = normalized
                cached = cache.get(chunk.id)
                if cached is None:
                    session.add(DocumentRetrievalChunkEmbedding(
                        chunk_id=chunk.id, embedding_model=model,
                        content_sha256=chunk.content_sha256, embedding=normalized,
                    ))
                else:
                    cached.content_sha256 = chunk.content_sha256
                    cached.embedding = normalized
        session.commit()
    coverage = len(vectors) / max(len(chunks), 1)
    return vectors, coverage


def _rrf_merge(rankings: list[list[DocumentRetrievalChunk]], rrf_k: int) -> list[tuple[DocumentRetrievalChunk, float]]:
    scores: dict[UUID, float] = defaultdict(float)
    items: dict[UUID, DocumentRetrievalChunk] = {}
    for ranking in rankings:
        for position, chunk in enumerate(ranking, start=1):
            scores[chunk.id] += 1.0 / (rrf_k + position)
            items[chunk.id] = chunk
    merged = [(items[chunk_id], score) for chunk_id, score in scores.items()]
    merged.sort(key=lambda item: item[1], reverse=True)
    return merged


def retrieve_tender_evidence(session: Session, tender_id: UUID, question: str) -> RetrievalResult:
    start = time.perf_counter()
    settings = get_settings()
    trace = RetrievalTrace()
    model = getattr(settings, "nvidia_embedding_model", "nvidia/nemotron-3-embed-1b")
    candidate_k = max(5, min(200, int(getattr(settings, "tenderwise_qa_candidate_k", 50))))
    trace.embedding_model = model
    chunks, current_item_ids = _load_current_chunks(session, tender_id, settings)
    trace.chunks_scanned = len(chunks)
    if not chunks:
        trace.retrieval_ms = int((time.perf_counter() - start) * 1000)
        return RetrievalResult([], "none", 0, trace)

    topics = _detect_topics(question)
    variants = _query_variants(question, topics)
    trace.query_variant_count = len(variants)
    trace.query_strategy = "original_plus_rule_based_subqueries" if len(variants) > 1 else "original_question"
    original_tokens = _tokens(question)
    expanded_tokens = set(original_tokens)
    for topic in topics:
        expanded_tokens.update(TOPIC_GROUPS.get(topic, set()))
    lexical_tokens = expanded_tokens or original_tokens

    filenames = session.execute(
        select(DocumentRetrievalChunk.id, TenderDocument.original_filename)
        .join(TenderDocument, TenderDocument.id == DocumentRetrievalChunk.document_id)
        .where(DocumentRetrievalChunk.id.in_([chunk.id for chunk in chunks]))
    ).all()
    filename_by_chunk_id = {chunk_id: filename for chunk_id, filename in filenames}

    lexical_scores = _bm25_scores(lexical_tokens, [chunk.content_text for chunk in chunks])
    lexical_pairs = sorted(zip(chunks, lexical_scores), key=lambda pair: pair[1], reverse=True)
    lexical_ranked = [chunk for chunk, score in lexical_pairs if score > 0][:candidate_k]
    trace.lexical_candidates = len(lexical_ranked)

    semantic_enabled = bool(
        getattr(settings, "tenderwise_semantic_retrieval_enabled", False)
        and getattr(settings, "nvidia_api_key", None)
    )
    reranker_enabled = bool(
        getattr(settings, "tenderwise_reranker_enabled", False)
        and getattr(settings, "nvidia_api_key", None)
    )
    rankings: list[list[DocumentRetrievalChunk]] = [lexical_ranked]
    semantic_count = 0
    retrieval_mode = "keyword_fallback"
    vector_by_id: dict[UUID, list[float]] = {}
    dense_ranked_ids: list[UUID] = []

    if semantic_enabled:
        try:
            max_backfill = getattr(settings, "tenderwise_qa_max_vector_backfill_per_query", 256)
            vector_by_id, trace.embedding_coverage = _ensure_chunk_embeddings(
                session, chunks, model, question, lexical_ranked, max_backfill,
            )
            query_vectors = generate_embeddings(variants, input_type="query", model=model)
            if len(query_vectors) != len(variants) or any(not _valid_vector(value) for value in query_vectors):
                raise EmbeddingServiceError("Embedding provider returned invalid query vectors.")

            dense_rankings: list[list[DocumentRetrievalChunk]] = []
            if session.get_bind().dialect.name == "postgresql":
                # Apply ANN search breadth only to this transaction/query. A
                # wider candidate list improves recall for tender-scoped
                # filters at additional query cost.
                hnsw_ef_search = int(getattr(settings, "tenderwise_qa_hnsw_ef_search", 100))
                session.execute(
                    text("SELECT set_config('hnsw.ef_search', :setting, true)"),
                    {"setting": str(max(40, min(1000, hnsw_ef_search)))},
                )
                chunk_ids = [chunk.id for chunk in chunks]
                for query_vector in query_vectors:
                    distance = DocumentRetrievalChunkEmbedding.embedding.cosine_distance(query_vector)
                    statement = (
                        select(DocumentRetrievalChunk)
                        .join(DocumentRetrievalChunkEmbedding, DocumentRetrievalChunkEmbedding.chunk_id == DocumentRetrievalChunk.id)
                        .where(
                            DocumentRetrievalChunk.id.in_(chunk_ids),
                            DocumentRetrievalChunkEmbedding.embedding_model == model,
                        )
                        .order_by(distance)
                        .limit(candidate_k)
                    )
                    dense_rankings.append(list(session.scalars(statement).all()))
            else:
                for query_vector in query_vectors:
                    scored = [
                        (chunk, _cosine_similarity(query_vector, vector_by_id[chunk.id]))
                        for chunk in chunks if chunk.id in vector_by_id
                    ]
                    scored.sort(key=lambda pair: pair[1], reverse=True)
                    dense_rankings.append([chunk for chunk, score in scored[:candidate_k] if score > 0])
            # Include focused subqueries as independent dense rankings.
            rankings.extend(dense_rankings)
            dense_ranked_ids = list(dict.fromkeys(chunk.id for ranking in dense_rankings for chunk in ranking))
            semantic_count = len(dense_ranked_ids)
            trace.dense_candidates = semantic_count
            if semantic_count:
                retrieval_mode = "hybrid"
        except Exception as exc:
            logger.warning("Dense retrieval failed; falling back to BM25. tender_id=%s error=%s", tender_id, exc)
            vector_by_id = {}
            trace.embedding_coverage = 0.0
            semantic_count = 0
            trace.dense_candidates = 0
            rankings = [lexical_ranked]
            retrieval_mode = "keyword_fallback"

    fused = _rrf_merge(rankings, getattr(settings, "tenderwise_qa_rrf_k", 60))
    fused = fused[:candidate_k]
    trace.fused_candidates = len(fused)

    rerank_mode = "disabled"
    if reranker_enabled and fused:
        rerank_start = time.perf_counter()
        try:
            reranked = rerank_passages(
                question,
                [chunk.content_text[:MAX_SOURCE_CHARS] for chunk, _score in fused],
                model=getattr(settings, "tenderwise_reranker_model", "nvidia/nemotron-3.5-rerank-8b"),
                endpoint=getattr(settings, "tenderwise_reranker_url", "https://ai.api.nvidia.com/v1/retrieval/nvidia/nemotron-3.5-rerank-8b/reranking"),
            )
            ordered: list[tuple[DocumentRetrievalChunk, float]] = []
            returned_indices: set[int] = set()
            for index, logit in reranked:
                if 0 <= index < len(fused) and index not in returned_indices:
                    ordered.append((fused[index][0], logit))
                    returned_indices.add(index)
            if ordered:
                # A provider response may be partial; keep unranked candidates
                # after the explicitly ranked set instead of silently dropping them.
                ordered.extend(
                    item for index, item in enumerate(fused) if index not in returned_indices
                )
                fused = ordered
                rerank_mode = "nvidia_cross_encoder"
            else:
                rerank_mode = "rrf_only"
        except AIServiceError as exc:
            logger.warning("Cross-encoder reranking unavailable; retaining RRF order: %s", exc)
            rerank_mode = "rrf_only"
        finally:
            trace.rerank_ms = int((time.perf_counter() - rerank_start) * 1000)
    elif reranker_enabled:
        rerank_mode = "rrf_only"
    trace.rerank_mode = rerank_mode
    trace.ranked_evidence = [
        {
            "chunk_id": str(chunk.id),
            "document_name": filename_by_chunk_id.get(chunk.id, "Unknown tender document"),
            "source_ref": chunk.source_ref or {},
            "source_refs": chunk.source_refs or [],
            "score": float(score),
        }
        for chunk, score in fused
    ]

    selected: list[dict[str, Any]] = []
    selected_ids: set[UUID] = set()
    total_chars = 0
    for chunk, score in fused:
        if chunk.id in selected_ids or len(selected) >= MAX_EVIDENCE_SOURCES:
            continue
        content = chunk.content_text[:MAX_SOURCE_CHARS]
        if total_chars + len(content) > MAX_TOTAL_CONTEXT_CHARS:
            continue
        filename = filename_by_chunk_id.get(chunk.id, "Unknown tender document")
        refs = chunk.source_refs if isinstance(chunk.source_refs, list) else []
        selected.append({
            "chunk": chunk,
            "filename": filename,
            "content": content,
            "score": float(score),
            "source_ref": chunk.source_ref or (refs[0] if refs else {}),
            "source_refs": refs,
            "source_unit_ids": chunk.source_unit_ids or [],
        })
        selected_ids.add(chunk.id)
        total_chars += len(content)
    trace.selected_sources = len(selected)
    trace.retrieval_ms = int((time.perf_counter() - start) * 1000) - trace.rerank_ms
    trace.retrieval_ms = max(0, trace.retrieval_ms)
    return RetrievalResult(selected, retrieval_mode if selected else "none", semantic_count, trace)
