"""API contracts for manually verified pre-submission checklist items."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


VerificationStatus = Literal["pending", "in_progress", "verified", "blocked", "not_applicable"]


class VerificationItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    requirement_key: str
    category: str
    requirement: str
    required_action: str
    evidence_status: str
    verification_status: VerificationStatus
    source_ids: list[str] = Field(default_factory=list)
    candidate_documents: list[dict[str, Any]] = Field(default_factory=list)
    verification_notes: str | None = None
    reviewer_name: str | None = None
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class VerificationChecklistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tender_id: UUID
    company_id: UUID | None = None
    evidence_matching_run_id: UUID | None = None
    readiness_status: str
    item_count: int
    verified_count: int
    pending_count: int
    blocked_count: int
    created_at: datetime
    updated_at: datetime
    items: list[VerificationItemResponse] = Field(default_factory=list)


class VerificationItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_status: VerificationStatus
    verification_notes: str | None = Field(default=None, max_length=5000)
    reviewer_name: str | None = Field(default=None, max_length=255)
