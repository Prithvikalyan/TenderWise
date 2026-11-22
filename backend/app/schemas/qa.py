from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QARequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=2000)
    conversation_id: UUID | None = None


class QASource(BaseModel):
    source_id: str
    document_name: str
    unit_type: str
    location: dict[str, Any]
    citation: str
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    chunk_id: str | None = None


class QAResponse(BaseModel):
    question: str
    answer: str
    sources: list[QASource]
    evidence_found: bool
    retrieval_mode: Literal["hybrid", "keyword_fallback", "none"] = "none"
    semantic_candidate_count: int = 0
    conversation_id: UUID | None = None
    rerank_mode: Literal["nvidia_cross_encoder", "rrf_only", "disabled"] = "disabled"
    citation_validation_status: Literal["valid_structure", "needs_review", "no_sources"] = "no_sources"
    citation_validation_details: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)


class QAConversationSummary(BaseModel):
    id: UUID
    tender_id: UUID
    title: str
    created_at: Any
    updated_at: Any
    message_count: int = 0


class QAConversationMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    response_json: dict[str, Any] | None = None
    created_at: Any


class QAConversationDetail(QAConversationSummary):
    messages: list[QAConversationMessage] = Field(default_factory=list)


class QARetrievalMetrics(BaseModel):
    total_questions: int
    evidence_found_count: int
    no_evidence_count: int
    hybrid_count: int
    keyword_fallback_count: int
    average_retrieval_ms: float
    average_rerank_ms: float
    average_generation_ms: float
    average_embedding_coverage: float
    citation_needs_review_count: int
