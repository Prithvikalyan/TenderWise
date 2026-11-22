from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.tender import TenderStatus


class TenderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    reference_number: str | None = Field(default=None, max_length=255)
    issuing_organization: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Tender name must not be empty")
        return cleaned


class TenderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    reference_number: str | None = Field(default=None, max_length=255)
    issuing_organization: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    description: str | None = None
    status: TenderStatus | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Tender name cannot be null")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Tender name must not be empty")
        return cleaned

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: TenderStatus | None) -> TenderStatus:
        if value is None:
            raise ValueError("Tender status cannot be null")
        return value

    @model_validator(mode="after")
    def require_editable_field(self) -> "TenderUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one editable field is required")
        return self


class TenderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    reference_number: str | None
    issuing_organization: str | None
    department: str | None
    description: str | None
    status: TenderStatus
    created_at: datetime
    updated_at: datetime


class TenderDetailResponse(TenderResponse):
    document_count: int


class TenderListResponse(BaseModel):
    items: list[TenderResponse]
    total: int
    limit: int
    offset: int
