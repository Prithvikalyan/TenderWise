
from typing import Any

from pydantic import BaseModel, ConfigDict


class AnalysisSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    document_name: str
    unit_type: str
    location: dict[str, Any]
    citation: str


class TenderAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tender_id: str
    report: str
    sources: list[AnalysisSource]
    evidence_found: bool
