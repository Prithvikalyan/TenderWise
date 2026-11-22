from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings


MAX_COMPANY_VERSIONS_PER_RUN = get_settings().tenderwise_processing_max_documents_per_run


class CompanyProcessingRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_ids: list[UUID] = Field(min_length=1, max_length=MAX_COMPANY_VERSIONS_PER_RUN)

    @field_validator("version_ids")
    @classmethod
    def validate_version_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("Company document version IDs must be unique")
        if len(values) > get_settings().tenderwise_processing_max_documents_per_run:
            raise ValueError("Too many company document versions selected")
        return values


class CompanyProcessingRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    status: str
    requested_document_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime




class CompanyProcessingRunListResponse(BaseModel):
    items: list[CompanyProcessingRunResponse]
    total: int
    page: int
    limit: int
    offset: int


class CompanyProcessingRunItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_document_version_id: UUID | None
    status: str
    outcome_code: str | None
    failure_class: str | None
    attempt_count: int
    max_attempts: int
    safe_message: str | None
    extraction_unit_count: int = 0
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class CompanyProcessingRunCreatedResponse(CompanyProcessingRunResponse):
    items: list[CompanyProcessingRunItemResponse]


class CompanyProcessingRunCounts(BaseModel):
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0


class CompanyProcessingRunDetailResponse(CompanyProcessingRunResponse):
    item_counts: CompanyProcessingRunCounts
    items: list[CompanyProcessingRunItemResponse]
    total_items: int
    page: int
    limit: int
    offset: int


class CompanyExtractionUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    unit_index: int
    unit_type: str
    source_ref: dict
    content_text: str | None
    table_data: dict | list | None
    metadata: dict = Field(validation_alias="metadata_json")
    created_at: datetime


class CompanyExtractionUnitListResponse(BaseModel):
    run_id: UUID
    company_document_id: UUID
    company_document_version_id: UUID
    items: list[CompanyExtractionUnitResponse]
    total: int
    page: int
    limit: int
    offset: int
