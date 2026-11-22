from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.tender_document import DocumentCategory


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tender_id: UUID
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    sha256: str | None
    category: DocumentCategory
    created_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class DocumentCategoryUpdate(BaseModel):
    category: DocumentCategory
