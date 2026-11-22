"""Persistent retrieval chunks, their vector cache, Q&A conversations and telemetry."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.vector_types import Vector
from app.models.tender import utc_now

_JSON = JSON().with_variant(JSONB, "postgresql")
EMBEDDING_DIMENSIONS = 2048


class DocumentRetrievalChunk(Base):
    """A structure-aware, source-preserving retrieval passage."""

    __tablename__ = "document_retrieval_chunks"
    __table_args__ = (
        UniqueConstraint("run_item_id", "chunk_index", "chunker_version", name="uq_retrieval_chunks_run_index_version"),
        Index("ix_retrieval_chunks_tender_run_item", "tender_id", "run_item_id"),
        Index("ix_retrieval_chunks_tender_document", "tender_id", "document_id"),
        Index("ix_retrieval_chunks_content_hash", "content_sha256"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("tender_documents.id", ondelete="CASCADE"), nullable=False)
    run_item_id: Mapped[UUID] = mapped_column(ForeignKey("document_processing_run_items.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(24), nullable=False, default="text", server_default=text("'text'"))
    section_heading: Mapped[str | None] = mapped_column(String(500))
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    source_refs: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    source_unit_ids: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())


class DocumentRetrievalChunkEmbedding(Base):
    """Model-versioned pgvector cache for retrieval chunks."""

    __tablename__ = "document_retrieval_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "embedding_model", name="uq_retrieval_chunk_embedding_model"),
        Index("ix_retrieval_chunk_embeddings_model", "embedding_model"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("document_retrieval_chunks.id", ondelete="CASCADE"), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())


class TenderQAConversation(Base):
    __tablename__ = "tender_qa_conversations"
    __table_args__ = (Index("ix_tender_qa_conversations_tender_updated", "tender_id", text("updated_at DESC")),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())


class TenderQAMessage(Base):
    __tablename__ = "tender_qa_messages"
    __table_args__ = (
        Index("ix_tender_qa_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_tender_qa_messages_tender_created", "tender_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("tender_qa_conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[dict | None] = mapped_column(_JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())


class QARetrievalLog(Base):
    """Privacy-minimal retrieval telemetry; never stores raw questions or answers."""

    __tablename__ = "qa_retrieval_logs"
    __table_args__ = (
        Index("ix_qa_retrieval_logs_tender_created", "tender_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tender_id: Mapped[UUID] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("tender_qa_conversations.id", ondelete="SET NULL"))
    question_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    rerank_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="disabled", server_default=text("'disabled'"))
    chunks_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lexical_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dense_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fused_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_coverage: Mapped[float] = mapped_column(nullable=False, default=0.0)
    retrieval_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rerank_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_found: Mapped[bool] = mapped_column(nullable=False, default=False)
    citation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_checked", server_default=text("'not_checked'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
