"""Pydantic contracts for evidence-grounded eligibility analysis."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EligibilityStatus = Literal[
    "met",
    "not_met",
    "evidence_missing",
    "needs_verification",
]


class EligibilitySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    document_name: str
    document_role: Literal["tender", "bidder"]
    unit_type: str
    location: dict[str, Any]
    citation: str


class EligibilityRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=3, max_length=1200)
    category: str = Field(min_length=2, max_length=80)
    status: EligibilityStatus
    explanation: str = Field(min_length=3, max_length=2000)
    required_evidence: list[str] = Field(default_factory=list, max_length=12)
    tender_source_ids: list[str] = Field(default_factory=list, max_length=10)
    bidder_source_ids: list[str] = Field(default_factory=list, max_length=10)
    company_evidence_status: str | None = Field(default=None, max_length=40)
    company_evidence_note: str | None = Field(default=None, max_length=2000)
    company_evidence_candidate_names: list[str] = Field(default_factory=list, max_length=10)
    company_evidence_pending_filenames: list[str] = Field(default_factory=list, max_length=10)


class EligibilityAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tender_id: str
    overall_status: Literal[
        "ready",
        "not_ready",
        "incomplete",
        "needs_review",
        "no_requirements_found",
    ]
    summary: str
    requirements: list[EligibilityRequirement]
    sources: list[EligibilitySource]
    evidence_found: bool
    bidder_evidence_found: bool
    # Diagnostic counts distinguish "no text extracted" from "text extracted,
    # but no eligibility clause could be safely identified".
    extracted_unit_count: int = 0
    reviewed_source_count: int = 0
