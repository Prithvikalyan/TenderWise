from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MilestoneCategoryValue = Literal[
    "submission_deadline",
    "clarification_deadline",
    "pre_bid_meeting",
    "bid_opening",
    "emd_deadline",
    "document_deadline",
    "other",
]


class MilestoneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    category: MilestoneCategoryValue = "other"
    due_date: date
    notes: str | None = Field(default=None, max_length=5000)
    source_reference: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Milestone title cannot be empty")
        return value

    @field_validator("notes", "source_reference")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class MilestoneUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    category: MilestoneCategoryValue | None = None
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=5000)
    source_reference: str | None = Field(default=None, max_length=500)
    is_completed: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one milestone field must be provided")
        for required_field in ("title", "category", "due_date", "is_completed"):
            if required_field in self.model_fields_set and getattr(self, required_field) is None:
                raise ValueError(f"{required_field} cannot be null")
        if self.title is not None and not self.title.strip():
            raise ValueError("Milestone title cannot be empty")
        return self


class MilestoneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tender_id: UUID
    tender_name: str
    tender_reference: str | None
    title: str
    category: str
    due_date: date
    notes: str | None
    source_reference: str | None
    is_completed: bool
    created_at: datetime
    updated_at: datetime


class MilestoneListResponse(BaseModel):
    items: list[MilestoneResponse]
    total: int
