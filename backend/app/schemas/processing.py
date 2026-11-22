from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings

MAX_DOCUMENTS_PER_PROCESSING_RUN = (
    get_settings().tenderwise_processing_max_documents_per_run
)


class ProcessingRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[UUID] = Field(
        min_length=1, max_length=MAX_DOCUMENTS_PER_PROCESSING_RUN
    )

    @field_validator("document_ids")
    @classmethod
    def reject_duplicate_document_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("Document IDs must be unique")
        if len(values) > get_settings().tenderwise_processing_max_documents_per_run:
            raise ValueError("Too many documents selected for processing")
        return values


class ProcessingRunItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID | None
    company_document_version_id: UUID | None = None
    status: str
    outcome_code: str | None
    failure_class: str | None
    attempt_count: int
    max_attempts: int
    safe_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ProcessingRunCounts(BaseModel):
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0


class ProcessingRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tender_id: UUID
    status: str
    requested_document_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class ProcessingRunCreatedResponse(ProcessingRunResponse):
    items: list[ProcessingRunItemResponse]


class ProcessingRunListResponse(BaseModel):
    items: list[ProcessingRunResponse]
    total: int
    page: int
    limit: int
    offset: int


class ProcessingRunDetailResponse(ProcessingRunResponse):
    item_counts: ProcessingRunCounts
    items: list[ProcessingRunItemResponse]
    total_items: int
    page: int
    limit: int
    offset: int


class ProcessingDocumentHistoryEntry(BaseModel):
    run_id: UUID
    run_status: str
    requested_document_count: int
    run_created_at: datetime
    run_started_at: datetime | None
    run_completed_at: datetime | None
    run_updated_at: datetime
    item_id: UUID
    document_id: UUID
    item_status: str
    outcome_code: str | None
    failure_class: str | None
    attempt_count: int
    max_attempts: int
    safe_message: str | None
    item_created_at: datetime
    item_started_at: datetime | None
    item_completed_at: datetime | None


class ProcessingDocumentHistoryResponse(BaseModel):
    items: list[ProcessingDocumentHistoryEntry]
    total: int
    page: int
    limit: int
    offset: int


class ExtractionPreviewUnit(BaseModel):
    unit_index: int
    unit_type: str
    citation: str
    content_text: str


class ProcessingItemExtractionResponse(BaseModel):
    run_id: UUID
    item_id: UUID
    document_id: UUID
    filename: str
    item_status: str
    outcome_code: str | None
    extraction_unit_count: int
    has_extracted_text: bool
    units_truncated: bool
    units: list[ExtractionPreviewUnit]
