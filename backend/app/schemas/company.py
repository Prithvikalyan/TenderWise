from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.company import CompanyDocumentCategory


class CompanyCreate(BaseModel):
    legal_name: str = Field(min_length=2, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    registration_number: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("legal_name")
    @classmethod
    def clean_legal_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Legal name must contain at least two characters")
        return value


class CompanyUpdate(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    registration_number: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)
    is_archived: bool | None = None

    @field_validator("legal_name")
    @classmethod
    def clean_legal_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Legal name must contain at least two characters")
        return value


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    legal_name: str
    display_name: str | None
    registration_number: str | None
    notes: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class CompanyListResponse(BaseModel):
    items: list[CompanyResponse]
    total: int
    limit: int
    offset: int


class CompanyDocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    version_number: int
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    sha256: str | None
    issue_date: date | None
    expiry_date: date | None
    financial_year: str | None
    issuer: str | None
    reference_number: str | None
    notes: str | None
    created_at: datetime


class CompanyDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    company_id: UUID
    title: str
    category: CompanyDocumentCategory
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    current_version: CompanyDocumentVersionResponse | None
    version_count: int


class CompanyDocumentListResponse(BaseModel):
    items: list[CompanyDocumentResponse]
    total: int
    limit: int
    offset: int


class CompanyDocumentVersionListResponse(BaseModel):
    items: list[CompanyDocumentVersionResponse]
    total: int


class CompanyDocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    is_archived: bool | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be blank")
        return value


class CompanyDocumentVersionMetadataUpdate(BaseModel):
    issue_date: date | None = None
    expiry_date: date | None = None
    financial_year: str | None = Field(default=None, max_length=20)
    issuer: str | None = Field(default=None, max_length=255)
    reference_number: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.issue_date and self.expiry_date and self.expiry_date < self.issue_date:
            raise ValueError("Expiry date cannot be earlier than issue date")
        return self
