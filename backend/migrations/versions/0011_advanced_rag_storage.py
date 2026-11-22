"""Add structured retrieval chunks, pgvector cache, chat history and telemetry.

Revision ID: 0011_advanced_rag_storage
Revises: 0010_tender_milestones
Create Date: 2026-11-22

This migration is additive. Existing extraction units and their JSON embedding
cache are retained, so the application can roll back without losing originals.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0011_advanced_rag_storage"
down_revision: Union[str, None] = "0010_tender_milestones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class _Vector2048(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **_kw):
        return "VECTOR(2048)"


def upgrade() -> None:
    # This fails with an explicit PostgreSQL error when pgvector's server files
    # are not installed, rather than silently creating a JSON-only vector store.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_retrieval_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("run_item_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_type", sa.String(length=24), server_default="text", nullable=False),
        sa.Column("section_heading", sa.String(length=500), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("source_ref", _json_type(), nullable=False),
        sa.Column("source_refs", _json_type(), nullable=False),
        sa.Column("source_unit_ids", _json_type(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunker_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["tender_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_item_id"], ["document_processing_run_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_item_id", "chunk_index", "chunker_version", name="uq_retrieval_chunks_run_index_version"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_retrieval_chunks_index_nonnegative"),
        sa.CheckConstraint("token_count > 0", name="ck_retrieval_chunks_tokens_positive"),
    )
    op.create_index("ix_retrieval_chunks_tender_run_item", "document_retrieval_chunks", ["tender_id", "run_item_id"])
    op.create_index("ix_retrieval_chunks_tender_document", "document_retrieval_chunks", ["tender_id", "document_id"])
    op.create_index("ix_retrieval_chunks_content_hash", "document_retrieval_chunks", ["content_sha256"])

    op.create_table(
        "document_retrieval_chunk_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", _Vector2048(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_retrieval_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "embedding_model", name="uq_retrieval_chunk_embedding_model"),
    )
    op.create_index("ix_retrieval_chunk_embeddings_model", "document_retrieval_chunk_embeddings", ["embedding_model"])
    # Nemotron-3-Embed-1B returns 2048 dimensions. pgvector's full-precision
    # vector HNSW index dimension ceiling is lower, so index a halfvec cast.
    op.execute(
        "CREATE INDEX ix_retrieval_chunk_embeddings_hnsw "
        "ON document_retrieval_chunk_embeddings USING hnsw "
        "((embedding::halfvec(2048)) halfvec_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "tender_qa_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False, server_default="New conversation"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tender_qa_conversations_tender_updated", "tender_qa_conversations", ["tender_id", sa.text("updated_at DESC")])

    op.create_table(
        "tender_qa_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("response_json", _json_type(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["tender_qa_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_tender_qa_messages_role"),
    )
    op.create_index("ix_tender_qa_messages_conversation_created", "tender_qa_messages", ["conversation_id", "created_at"])
    op.create_index("ix_tender_qa_messages_tender_created", "tender_qa_messages", ["tender_id", "created_at"])

    op.create_table(
        "qa_retrieval_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("question_sha256", sa.String(length=64), nullable=False),
        sa.Column("retrieval_mode", sa.String(length=32), nullable=False),
        sa.Column("rerank_mode", sa.String(length=32), nullable=False, server_default="disabled"),
        sa.Column("chunks_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lexical_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dense_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fused_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("retrieval_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rerank_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generation_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_found", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("citation_status", sa.String(length=32), nullable=False, server_default="not_checked"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["tender_qa_conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_retrieval_logs_tender_created", "qa_retrieval_logs", ["tender_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_qa_retrieval_logs_tender_created", table_name="qa_retrieval_logs")
    op.drop_table("qa_retrieval_logs")
    op.drop_index("ix_tender_qa_messages_tender_created", table_name="tender_qa_messages")
    op.drop_index("ix_tender_qa_messages_conversation_created", table_name="tender_qa_messages")
    op.drop_table("tender_qa_messages")
    op.drop_index("ix_tender_qa_conversations_tender_updated", table_name="tender_qa_conversations")
    op.drop_table("tender_qa_conversations")
    op.drop_index("ix_retrieval_chunk_embeddings_hnsw", table_name="document_retrieval_chunk_embeddings")
    op.drop_index("ix_retrieval_chunk_embeddings_model", table_name="document_retrieval_chunk_embeddings")
    op.drop_table("document_retrieval_chunk_embeddings")
    op.drop_index("ix_retrieval_chunks_content_hash", table_name="document_retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_tender_document", table_name="document_retrieval_chunks")
    op.drop_index("ix_retrieval_chunks_tender_run_item", table_name="document_retrieval_chunks")
    op.drop_table("document_retrieval_chunks")
    # Leave the shared PostgreSQL extension installed; other applications may use it.
