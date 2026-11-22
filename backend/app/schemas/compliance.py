"""Pydantic contracts for structured, evidence-grounded compliance analysis."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ComplianceStatus = Literal[
    "compliant",
    "non_compliant",
    "evidence_missing",
    "needs_verification",
]


class ComplianceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    document_name: str
    document_role: Literal["tender", "bidder"]
    unit_type: str
    location: dict[str, Any]
    citation: str


class ComplianceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=3, max_length=1200)
    category: str = Field(min_length=2, max_length=80)
    status: ComplianceStatus
    explanation: str = Field(min_length=3, max_length=2000)
    required_action: str = Field(default="", max_length=1200)
    tender_source_ids: list[str] = Field(default_factory=list, max_length=10)
    bidder_source_ids: list[str] = Field(default_factory=list, max_length=10)
    company_evidence_status: str | None = Field(default=None, max_length=40)
    company_evidence_note: str | None = Field(default=None, max_length=2000)
    company_evidence_candidate_names: list[str] = Field(default_factory=list, max_length=10)
    company_evidence_pending_filenames: list[str] = Field(default_factory=list, max_length=10)


class TenderInformationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=3, max_length=1000)
    category: Literal[
        "submission_deadline",
        "clarification_deadline",
        "bid_opening",
        "other",
    ]
    details: str = Field(min_length=1, max_length=1200)
    tender_source_ids: list[str] = Field(default_factory=list, max_length=10)


class PostAwardObligation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obligation: str = Field(min_length=3, max_length=1200)
    category: str = Field(min_length=2, max_length=80)
    details: str = Field(min_length=1, max_length=1600)
    tender_source_ids: list[str] = Field(default_factory=list, max_length=10)


class ComplianceAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tender_id: str
    overall_status: Literal[
        "compliant",
        "non_compliant",
        "incomplete",
        "needs_review",
        "no_requirements_found",
    ]
    summary: str
    requirements: list[ComplianceRequirement]
    tender_information: list[TenderInformationItem]
    post_award_obligations: list[PostAwardObligation]
    sources: list[ComplianceSource]
    evidence_found: bool
    bidder_evidence_found: bool
